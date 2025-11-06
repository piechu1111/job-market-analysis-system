from pymongo import MongoClient
import psycopg2
from config import MONGO_URI, POSTGRES_URL, logger

mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client["jobs_db"]
collection = mongo_db["muse_offers_enriched"]


def load_muse_jobs_to_postgresql():
    pg_conn = psycopg2.connect(POSTGRES_URL)
    pg_cur = pg_conn.cursor()

    # create tables
    pg_cur.execute(
        """
    CREATE TABLE IF NOT EXISTS companies (
        id SERIAL PRIMARY KEY,
        company_id INT UNIQUE,
        name TEXT,
        short_name TEXT
    );

    CREATE TABLE IF NOT EXISTS jobs (
        id SERIAL PRIMARY KEY,
        job_id INT UNIQUE,
        title TEXT,
        company_id INT REFERENCES companies(id),
        description TEXT,
        publication_date TIMESTAMP,
        fetched_at TIMESTAMP,
        source TEXT,
        url TEXT,
        short_name TEXT,
        model_type TEXT,
        type TEXT
    );

    CREATE TABLE IF NOT EXISTS locations (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE
    );

    CREATE TABLE IF NOT EXISTS job_locations (
        job_id INT REFERENCES jobs(id),
        location_id INT REFERENCES locations(id),
        PRIMARY KEY (job_id, location_id)
    );

    CREATE TABLE IF NOT EXISTS categories (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE
    );

    CREATE TABLE IF NOT EXISTS job_categories (
        job_id INT REFERENCES jobs(id),
        category_id INT REFERENCES categories(id),
        PRIMARY KEY (job_id, category_id)
    );

    CREATE TABLE IF NOT EXISTS skills (
        id SERIAL PRIMARY KEY,
        name TEXT,
        category TEXT,
        UNIQUE(name, category)
    );

    CREATE TABLE IF NOT EXISTS job_skills (
        job_id INT REFERENCES jobs(id),
        skill_id INT REFERENCES skills(id),
        PRIMARY KEY (job_id, skill_id)
    );

    CREATE TABLE IF NOT EXISTS auth_tokens (
        token TEXT PRIMARY KEY,
        expires_at TIMESTAMPTZ NOT NULL
    );

    """
    )
    pg_conn.commit()

    company_cache = {}
    location_cache = {}
    category_cache = {}
    skill_cache = {}

    def get_or_create_company(doc):
        comp = doc.get("company")
        if not comp:
            return None
        cid = comp.get("id")
        if cid in company_cache:
            return company_cache[cid]
        pg_cur.execute(
            """
            INSERT INTO companies (company_id, name, short_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (company_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id;
        """,
            (cid, comp.get("name"), comp.get("short_name")),
        )
        comp_id = pg_cur.fetchone()[0]
        pg_conn.commit()
        company_cache[cid] = comp_id
        return comp_id

    def get_or_create_location(name):
        if name in location_cache:
            return location_cache[name]
        pg_cur.execute(
            """
            INSERT INTO locations (name)
            VALUES (%s)
            ON CONFLICT (name) DO NOTHING
            RETURNING id;
        """,
            (name,),
        )
        res = pg_cur.fetchone()
        if res:
            loc_id = res[0]
        else:
            pg_cur.execute("SELECT id FROM locations WHERE name = %s", (name,))
            loc_id = pg_cur.fetchone()[0]
        pg_conn.commit()
        location_cache[name] = loc_id
        return loc_id

    def get_or_create_category(name):
        if name in category_cache:
            return category_cache[name]
        pg_cur.execute(
            """
            INSERT INTO categories (name)
            VALUES (%s)
            ON CONFLICT (name) DO NOTHING
            RETURNING id;
        """,
            (name,),
        )
        res = pg_cur.fetchone()
        if res:
            cat_id = res[0]
        else:
            pg_cur.execute("SELECT id FROM categories WHERE name = %s", (name,))
            cat_id = pg_cur.fetchone()[0]
        pg_conn.commit()
        category_cache[name] = cat_id
        return cat_id

    def get_or_create_skill(name, category):
        key = (name, category)
        if key in skill_cache:
            return skill_cache[key]
        pg_cur.execute(
            """
            INSERT INTO skills (name, category)
            VALUES (%s, %s)
            ON CONFLICT (name, category) DO NOTHING
            RETURNING id;
        """,
            (name, category),
        )
        res = pg_cur.fetchone()
        if res:
            skill_id = res[0]
        else:
            pg_cur.execute(
                """
                SELECT id FROM skills WHERE name = %s AND category = %s;
            """,
                (name, category),
            )
            skill_id = pg_cur.fetchone()[0]
        pg_conn.commit()
        skill_cache[key] = skill_id
        return skill_id

    migrated_count = 0
    # truncate skills and job_skills to reflect latest changes from Mongo
    # no issue here as function execution time is low
    pg_cur.execute("TRUNCATE TABLE job_skills RESTART IDENTITY CASCADE;")
    pg_cur.execute("TRUNCATE TABLE skills RESTART IDENTITY CASCADE;")
    pg_conn.commit()

    # clear skill cache since skills table is empty
    skill_cache.clear()

    for doc in collection.find({}):
        company_id = get_or_create_company(doc)

        pg_cur.execute(
            """
                INSERT INTO jobs (job_id, title, company_id, description, publication_date,
                    fetched_at, source, url, short_name, model_type, type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (job_id) DO NOTHING
                RETURNING id;
            """,
            (
                doc.get("id"),
                doc.get("name"),
                company_id,
                doc.get("cleaned_description"),
                doc.get("publication_date"),
                doc.get("fetched_at"),
                doc.get("source"),
                doc.get("refs", {}).get("landing_page"),
                doc.get("short_name"),
                doc.get("model_type"),
                doc.get("type"),
            ),
        )
        job_res = pg_cur.fetchone()
        if job_res:
            job_row_id = job_res[0]
        else:
            # record already exists, so fetch its ID
            pg_cur.execute("SELECT id FROM jobs WHERE job_id = %s", (doc.get("id"),))
            job_row = pg_cur.fetchone()
            if not job_row:
                logger.warning(
                    f"Could not find job with job_id={doc.get('id')} after conflict."
                )
                continue
            job_row_id = job_row[0]
        pg_conn.commit()
        migrated_count += 1

        for loc in doc.get("locations", []):
            loc_id = get_or_create_location(loc.get("name"))
            pg_cur.execute(
                """
                INSERT INTO job_locations (job_id, location_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """,
                (job_row_id, loc_id),
            )
        pg_conn.commit()

        categories = doc.get("categories", [])
        if not categories:
            # fallback for missing category
            cat_id = get_or_create_category("Unknown")
            pg_cur.execute(
                """
                INSERT INTO job_categories (job_id, category_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """,
                (job_row_id, cat_id),
            )
            pg_conn.commit()
        else:
            for cat in categories:
                cat_id = get_or_create_category(cat.get("name"))
                pg_cur.execute(
                    """
                    INSERT INTO job_categories (job_id, category_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING;
                """,
                    (job_row_id, cat_id),
                )
            pg_conn.commit()

        categories = doc.get("categories", [])
        skill_category = categories[0]["name"] if categories else "Unknown"

        for skill_name in doc.get("merged_skills", []):
            if not skill_name:
                continue
            logger.info(
                f"Inserting skill: '{skill_name}' (category: '{skill_category}') for job {doc.get('id')}"
            )
            skill_id = get_or_create_skill(skill_name, skill_category)
            pg_cur.execute(
                """
                INSERT INTO job_skills (job_id, skill_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """,
                (job_row_id, skill_id),
            )
        pg_conn.commit()

    pg_cur.close()
    pg_conn.close()
    return {"status": "ok", "migrated_jobs": migrated_count}
