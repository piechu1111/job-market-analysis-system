import asyncio
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk, BulkIndexError
from tqdm import tqdm
import json
import re
from pymongo import MongoClient
from bs4 import BeautifulSoup
from config import MONGO_URI, ES_URL, logger

# connect to MongoDB
mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client["jobs_db"]
# connect to Elasticsearch
es = Elasticsearch(ES_URL, verify_certs=False)
# set up MongoDB collections
# if they don't exist, MongoDB will create them automatically when documents are inserted
mongo_raw_muse_offers_collection = mongo_db["raw_muse_offers"]
cleaned_muse_offers_collection = mongo_db["muse_offers_no_html"]
enriched_muse_offers_collection = mongo_db["muse_offers_enriched"]


# HTML cleaning
def _clean_html(raw_html: str) -> str:
    return BeautifulSoup(raw_html, "lxml").get_text(separator=" ", strip=True)


def create_or_update_cleaned_collection():
    logger.info(
        "Syncing documents from 'raw_muse_offers' to 'muse_offers_no_html' and cleaning HTML where needed..."
    )

    inserted = 0
    updated = 0

    raw_cursor = mongo_raw_muse_offers_collection.find()

    for doc in raw_cursor:
        if not cleaned_muse_offers_collection.find_one({"_id": doc["_id"]}):
            cleaned_muse_offers_collection.insert_one(doc)
            inserted += 1

    logger.info(f"Inserted {inserted} new documents into 'muse_offers_no_html'.")

    # add 'cleaned_description' to documents that don't have it
    to_clean_cursor = cleaned_muse_offers_collection.find(
        {"cleaned_description": {"$exists": False}}
    )

    for doc in to_clean_cursor:
        if "contents" in doc:
            cleaned = _clean_html(doc["contents"])
            cleaned_muse_offers_collection.update_one(
                {"_id": doc["_id"]}, {"$set": {"cleaned_description": cleaned}}
            )
            logger.debug(f"Cleaned HTML for document ID: {doc.get('_id')}")
            updated += 1
        else:
            logger.warning(f"Missing 'contents' field in document ID: {doc.get('_id')}")

        if updated % 100 == 0:
            logger.info(f"Processed {updated} documents for cleaning...")

    # assign 'Unknown' category where missing or empty
    result = cleaned_muse_offers_collection.update_many(
        {"$or": [{"categories": {"$exists": False}}, {"categories": {"$size": 0}}]},
        {"$set": {"categories": [{"name": "Unknown"}]}},
    )
    logger.info(f"Assigned 'Unknown' category to {result.modified_count} documents.")

    logger.info(f"HTML cleaning complete. Updated {updated} documents.")

    # ensure index on cleaned_description
    cleaned_muse_offers_collection.create_index("cleaned_description")

    return {
        "status": "ok",
        "inserted_documents": inserted,
        "updated_documents": updated,
    }


# enrich skills using simple Python matching
def _enrich_skills_simple(
    skills,
    category="Software Engineering",
    batch_size=10,
    overwrite=False,  # whether to overwrite existing matched_skills
    test_mode=True,
):
    SOURCE_COLLECTION = "muse_offers_no_html"
    TARGET_COLLECTION = "muse_offers_enriched"

    cleaned_muse_offers_collection = mongo_db[SOURCE_COLLECTION]
    enriched_muse_offers_collection = mongo_db[TARGET_COLLECTION]

    filter_query = {"categories.name": category}
    if test_mode:
        jobs_cursor = cleaned_muse_offers_collection.find(filter_query).limit(
            batch_size
        )
    else:
        jobs_cursor = cleaned_muse_offers_collection.find(filter_query)

    jobs = list(jobs_cursor)
    enriched_count = 0
    skipped = 0

    for job in jobs:
        job_id = job["_id"]
        existing_doc = enriched_muse_offers_collection.find_one({"_id": job_id})

        # skip document if it already has matched_skills and we're not overwriting
        if existing_doc and not overwrite and "matched_skills" in existing_doc:
            skipped += 1
            continue

        matched_skills = []
        description = job.get("cleaned_description", "")

        for skill in skills:
            if not skill:
                continue
            pattern = r"\b{}\b".format(re.escape(skill))
            if len(skill) <= 3:
                if re.search(pattern, description):
                    matched_skills.append(skill)
            else:
                if re.search(pattern, description, re.IGNORECASE):
                    matched_skills.append(skill)

        job["matched_skills"] = list(set(matched_skills))  # Unique skills

        # insert or update the document in the enriched collection
        enriched_muse_offers_collection.update_one(
            {"_id": job_id}, {"$set": job}, upsert=True
        )

        enriched_count += 1

    logger.info(
        f"Enriched {enriched_count} documents. Skipped {skipped} existing documents."
    )
    return {"enriched_count": enriched_count, "skipped_existing": skipped}


# this function enriches skills in batches for all categories defined in gpt_skills.json
def execute_enrichment_simple(batch_size=100, test_mode=False):
    with open("gpt_skills.json", "r", encoding="utf-8") as f:
        skills_data = json.load(f)

    results = {}
    for i, (category, skills) in enumerate(skills_data.items()):
        logger.info(f"\n=== Enriching category: {category} ({len(skills)} skills) ===")
        res = _enrich_skills_simple(
            skills=skills,
            category=category,
            batch_size=batch_size,
            overwrite=False,  # set to True if desired to overwrite existing matched_skills
            test_mode=test_mode,
        )
        results[category] = res

    # special case for Unknown category
    logger.info("\n=== Enriching 'Unknown' category ===")
    all_skills = sorted({s for skill_list in skills_data.values() for s in skill_list})
    res_unknown = _enrich_skills_simple(
        skills=all_skills,
        category="Unknown",
        batch_size=batch_size,
        overwrite=False,
        test_mode=test_mode,
    )
    results["Unknown"] = res_unknown

    return results


# index documents enriched by simple method from MongoDB to Elasticsearch
def index_mongo_to_elasticsearch(
    index_name="muse_offers_enriched", drop_index=False, force=False
):
    try:
        res = es.info()
        logger.info(f"ES info(): {res}")
    except Exception as e:
        logger.error(f"ES connection error: {e}")
        raise RuntimeError("Elasticsearch not reachable")

    logger.info(f"Index name: {index_name}")
    logger.info(f"ES URL: {ES_URL}")

    # optionally delete the index
    if drop_index:
        if es.indices.exists(index=index_name):
            logger.info(f"Dropping existing index: {index_name}")
            es.indices.delete(index=index_name)

    # create index if it doesn't exist
    if not es.indices.exists(index=index_name):
        logger.info(f"Creating index: {index_name}")
        mapping = {
            "mappings": {
                "properties": {
                    "id": {"type": "keyword"},
                    "name": {"type": "text"},
                    "company": {
                        "properties": {
                            "name": {"type": "text"},
                            "id": {"type": "long"},
                            "short_name": {"type": "keyword"},
                        }
                    },
                    "cleaned_description": {"type": "text"},
                    "publication_date": {"type": "date"},
                    "matched_skills": {"type": "keyword"},
                }
            }
        }
        es.indices.create(index=index_name, body=mapping)

    # get existing IDs from ES to avoid re-indexing
    existing_ids = set()
    if not force:
        try:
            es_ids = es.search(
                index=index_name, size=10000, source=False, query={"match_all": {}}
            )
            for hit in es_ids["hits"]["hits"]:
                existing_ids.add(hit["_id"])
        except Exception as e:
            logger.warning(f"Couldn't fetch existing IDs from ES: {e}")

    docs = []
    skipped = 0

    for doc in enriched_muse_offers_collection.find():
        mongo_id = str(doc.get("_id"))

        if not force and mongo_id in existing_ids:
            skipped += 1
            continue  # skip already indexed docs

        doc["id"] = mongo_id
        doc.pop("_id", None)

        docs.append(
            {
                "_index": index_name,
                "_id": doc["id"],
                "_source": doc,
            }
        )

    if not docs:
        logger.info("No new documents to index.")
        return {"indexed_docs": 0, "skipped": skipped}

    try:
        result = bulk(es, docs, raise_on_error=False)
        success_count = result[0]
        errors = [r for r in result[1] if r.get("index", {}).get("error")]

        if errors:
            logger.error(f"{len(errors)} documents failed to index.")
            for err in errors[:5]:
                logger.error(json.dumps(err, indent=2))

        logger.info(f"Successfully indexed {success_count} documents.")
    except BulkIndexError as e:
        logger.error(f"Bulk indexing error: {e}", exc_info=True)
        raise

    return {
        "indexed_docs": success_count,
        "skipped": skipped,
        "failed_docs": len(errors) if errors else 0,
    }


# enrich skills using Elasticsearch-based matching
# this function queries Elasticsearch for each job document and matches skills
# using fuzzy matching and phrase matching
# it splits skills into batches to avoid exceeding maxClauseCount in ES
# multimatch used for performance , lower skill_batch_size, better matching
def get_es_matched_skills_for_job(job_doc, skills, skill_batch_size=21):
    # splits skills into batches to avoid exceeding maxClauseCount in ES
    job_id = str(job_doc["_id"])
    matched_skills = set()
    if not job_doc.get("cleaned_description", ""):
        return []

    for i in range(0, len(skills), skill_batch_size):
        skills_batch = skills[i : i + skill_batch_size]
        should_clauses = []
        for skill in skills_batch:
            # if there is no skill or is shorter than 3 characters
            #  (simple matching will take these cases - ES catches too many false positives)
            if not skill or len(skill) <= 3:
                continue
            else:
                should_clauses.extend(
                    [
                        {
                            "match_phrase": {
                                "cleaned_description": {"query": skill, "slop": 2}
                            }
                        },
                        {
                            "match": {
                                "cleaned_description": {
                                    "query": skill,
                                    "fuzziness": "AUTO",
                                }
                            }
                        },
                    ]
                )

        query = {
            "bool": {
                "must": [{"term": {"id": job_id}}],
                "should": should_clauses,
                "minimum_should_match": 1,
            }
        }
        body = {
            "query": query,
            "highlight": {
                "fields": {
                    "cleaned_description": {
                        "number_of_fragments": 10,
                        "fragment_size": 200,
                    }
                }
            },
            "size": 1,
        }
        # send query only with skills batch
        try:
            res = es.search(
                index="muse_offers_enriched",
                body=body,
                timeout="30s",
                request_timeout=30,
            )
            if not res["hits"]["hits"]:
                continue
        except Exception as e:
            logger.warning(
                f"Error querying ES for job {job_id} with skills batch {skills_batch}: {e}"
            )
            continue  # we do not want to stop processing if one query fails

        highlight_fragments = (
            res["hits"]["hits"][0].get("highlight", {}).get("cleaned_description", [])
        )
        for skill in skills_batch:
            s = skill.lower().strip()
            for frag in highlight_fragments:
                frag_clean = frag.lower().strip()
                if re.search(r"\b" + re.escape(s) + r"\b", frag_clean):
                    matched_skills.add(skill)
                    break  # do not search this skill in this fragment anymore

    return list(matched_skills)


# this function enriches skills matched by Elasticsearch in batches for a single category
def enrich_es_matched_skills_batch(
    category="Software Engineering", batch_size=100, test_mode=True, overwrite=False
):
    with open("gpt_skills.json", "r", encoding="utf-8") as f:
        skills_data = json.load(f)
    skills = skills_data.get(category, [])

    if overwrite:
        filter_query = {"categories.name": category}
    else:
        filter_query = {
            "categories.name": category,
            "$or": [
                {"es_matched_skills": {"$exists": False}},
                {
                    "es_matched_skills": {"$size": 0},
                    "matched_skills": {"$exists": True, "$not": {"$size": 0}},
                },
            ],
        }

    cursor = enriched_muse_offers_collection.find(filter_query)
    if test_mode:
        cursor = cursor.limit(batch_size)

    jobs = list(cursor)
    total = len(jobs)
    logger.info(
        f"Processing {total} jobs in category '{category}' (overwrite={overwrite})"
    )

    for job in tqdm(jobs, desc=f"ES enrichment: {category}"):
        es_skills = get_es_matched_skills_for_job(job, skills)
        logger.info(f"matched ES skills: {es_skills}")
        skills_simple = job.get("matched_skills", [])
        merged_skills = sorted(set(es_skills) | set(skills_simple))

        enriched_muse_offers_collection.update_one(
            {"_id": job["_id"]},
            {"$set": {"merged_skills": merged_skills, "es_matched_skills": es_skills}},
        )
    logger.info("DONE.")


# Batch through all categories and enrich with ES
def enrich_es_all_categories(batch_size=20, test_mode=False):
    with open("gpt_skills.json", "r", encoding="utf-8") as f:
        skills_data = json.load(f)
    for cat in skills_data.keys():
        enrich_es_matched_skills_batch(
            category=cat,
            batch_size=batch_size,
            test_mode=test_mode,
            overwrite=False,  # Set to True if desired to overwrite existing es_matched_skills
        )
    logger.info("All categories processed!")


async def enrich_es_all_categories_async():
    with open("gpt_skills.json", "r", encoding="utf-8") as f:
        skills_data = json.load(f)

    categories = list(skills_data.keys())
    results = await _limited_gather(categories, limit=5)  # max 5 concurent categories
    return {"status": "ok", "categories_processed": len(results)}


# helper to limit number of concurrent tasks
async def _limited_gather(categories, limit=5):
    semaphore = asyncio.Semaphore(limit)

    # helper to run tasks with semaphore
    async def run_with_limit(category):
        async with semaphore:
            return await _async_enrich_category(category)

    # run all tasks with limited concurrency
    results = await asyncio.gather(
        *(run_with_limit(category) for category in categories),
        # do not stop on first exception, so other tasks can complete
        return_exceptions=True,
    )
    # log exceptions without stopping others tasks
    for result in results:
        if isinstance(result, Exception):
            logger.error("async category failed: %s", result)

    return results


# helper to run enrich_es_matched_skills_batch in asyncio
async def _async_enrich_category(category, batch_size=20, test_mode=False):
    return await asyncio.to_thread(
        enrich_es_matched_skills_batch,
        category=category,
        batch_size=batch_size,
        test_mode=test_mode,
        overwrite=False,
    )


# for Unknown category, we just populate merged_skills from matched_skills
# reason - performance as we have thousands of skills to match
def populate_merged_skills_for_unknown_category(
    batch_size=100, test_mode=False, overwrite=False
):
    filter_query = {
        "categories.name": "Unknown",
        "matched_skills": {"$exists": True, "$not": {"$size": 0}},
    }

    if not overwrite:
        filter_query["merged_skills"] = {"$exists": False}

    cursor = enriched_muse_offers_collection.find(filter_query)
    if test_mode:
        cursor = cursor.limit(batch_size)

    count = 0
    for doc in tqdm(cursor, desc="Processing 'Unknown' category"):
        merged_skills = doc.get("matched_skills", [])
        enriched_muse_offers_collection.update_one(
            {"_id": doc["_id"]}, {"$set": {"merged_skills": merged_skills}}
        )
        count += 1

    logger.info(f"Updated 'merged_skills' for {count} documents in 'Unknown' category.")
    return {"updated": count}


def execute_enrichment_es(category="Unknown", batch_size=20, test_mode=False):
    # unknown category is special case
    # we do not want to enrich it with ES, because of time/resource consumption
    # so we just populate merged_skills from matched_skills done by simple enrichment
    if category == "Unknown":
        result = populate_merged_skills_for_unknown_category(
            batch_size=batch_size, test_mode=test_mode, overwrite=False
        )
        return {
            "status": "ok",
            "message": f"Handled 'Unknown' category: {result['updated']} documents updated.",
        }

    enrich_es_matched_skills_batch(
        category=category,
        batch_size=batch_size,
        test_mode=test_mode,
        overwrite=False,  # Set to True if you want to overwrite existing es_matched_skills
    )

    return {"status": "ok", "message": "Enrichment with Elasticsearch completed."}


# indexing final enriched offers to Elasticsearch for search filed in Dash app
def index_for_final_search(index_name="muse_offers_final_search", drop_if_exists=True):
    if drop_if_exists and es.indices.exists(index=index_name):
        logger.info(f"Deleting existing index '{index_name}'")
        es.indices.delete(index=index_name)

    mapping = {
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "name": {"type": "text"},
                "cleaned_description": {"type": "text"},
                "publication_date": {"type": "date"},
                "url": {"type": "keyword"},
                "company": {
                    "properties": {
                        "name": {"type": "text"},
                        "short_name": {"type": "keyword"},
                    }
                },
                "categories": {
                    "type": "nested",
                    "properties": {"name": {"type": "keyword"}},
                },
                "locations": {
                    "type": "nested",
                    "properties": {"name": {"type": "keyword"}},
                },
                "merged_skills": {"type": "keyword"},
            }
        }
    }

    es.indices.create(index=index_name, body=mapping)
    logger.info(f"Created index '{index_name}'")

    actions = []
    for doc in enriched_muse_offers_collection.find():
        doc_id = str(doc["_id"])
        doc["id"] = doc_id
        doc.pop("_id", None)
        doc["url"] = doc.get("refs", {}).get("landing_page", "")

        actions.append({"_index": index_name, "_id": doc_id, "_source": doc})

    if actions:
        logger.info(f"Indexing {len(actions)} documents to '{index_name}'...")
        bulk(es, actions)
        logger.info("Indexing completed.")
