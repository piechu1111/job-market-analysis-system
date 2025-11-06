from fastapi import APIRouter, Query, Depends
from elasticsearch import Elasticsearch
from app.authentication import verify_token
from config import ES_URL

es = Elasticsearch(ES_URL, verify_certs=False)

router = APIRouter(
    prefix="/search", tags=["search"], dependencies=[Depends(verify_token)]
)


# search offers endpoint in Elasticsearch
@router.get("/offers")
def search_offers(q: str = Query(..., min_length=2), size: int = 10):
    body = {
        "query": {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": q,
                            "fields": [
                                "name^3",
                                "cleaned_description",
                                "company.name^2",
                                "merged_skills^4",
                            ],
                            "fuzziness": "AUTO",
                        }
                    },
                    {
                        "nested": {
                            "path": "locations",
                            "query": {
                                "match": {
                                    "locations.name": {
                                        "query": q,
                                        "fuzziness": "AUTO",
                                        "boost": 2,
                                    }
                                }
                            },
                        }
                    },
                    {
                        "nested": {
                            "path": "categories",
                            "query": {
                                "match": {
                                    "categories.name": {
                                        "query": q,
                                        "fuzziness": "AUTO",
                                        "boost": 2,
                                    }
                                }
                            },
                        }
                    },
                ]
            }
        },
        "highlight": {
            "fields": {
                "cleaned_description": {
                    "fragment_size": 150,
                    "number_of_fragments": 2,
                    "pre_tags": ["<mark>"],
                    "post_tags": ["</mark>"],
                },
                "name": {},
                "merged_skills": {},
                "company.name": {},
                "locations.name": {},
                "categories.name": {},
            }
        },
        "size": size,
    }

    res = es.search(index="muse_offers_final_search", body=body)
    results = []

    for hit in res["hits"]["hits"]:
        source = hit["_source"]
        highlight = hit.get("highlight", {})
        description = source.get("cleaned_description", "")
        short_desc = (
            (description[:100] + "...") if len(description) > 100 else description
        )

        results.append(
            {
                "title": source.get("name"),
                "company": source.get("company", {}).get("name"),
                "score": hit["_score"],
                "skills": source.get("merged_skills", []),
                "categories": [
                    category["name"] for category in source.get("categories", [])
                ],
                "locations": [
                    location["name"] for location in source.get("locations", [])
                ],
                "description": short_desc,
                "full_description": description,
                "url": source.get("refs", {}).get("landing_page", None),
                "highlight": highlight,
            }
        )

    return results
