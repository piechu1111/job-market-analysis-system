from typing import Optional
from fastapi import FastAPI, Query  # , Depends
from app.authentication import token_manager  # , verify_token
from fastapi.middleware.cors import CORSMiddleware
from app.adzuna_client import get_historical_average_salary_data, get_jobs_from_adzuna
from app.muse_client import get_jobs_from_muse, get_and_store_muse_jobs_multipage
from app.muse_processing import (
    create_or_update_cleaned_collection,
    index_mongo_to_elasticsearch,
    execute_enrichment_simple,
    execute_enrichment_es,
    enrich_es_all_categories,
    enrich_es_all_categories_async,
    index_for_final_search,
)
from app.muse_to_postgresql import load_muse_jobs_to_postgresql
from .analytics import router as analytics_router
from .es_search_jobs import router as search_router
from config import DASH_URL

app = FastAPI(title="Job API", version="1.0.0", docs_url="/docs")

# enable CORS for Dash app
app.add_middleware(
    CORSMiddleware, allow_origins=[DASH_URL], allow_methods=["*"], allow_headers=["*"]
)
# include the analytics router functions
# authentication is handled there
app.include_router(analytics_router)
# include the elasticsearch router function
# authentication is handled there
app.include_router(search_router)


# other endpoints
@app.get("/")
def root():
    return {"message": "Welcome"}


# Adzuna API endpoints, not used in the project, perhaps useful in the future
@app.get("/jobs")
def get_jobs(
    what: str = Query("python", description="Job title or keyword to search for."),
    location: str = Query("uk", description="Location to search for jobs."),
    country: str = Query("gb", description="Country code (e.g. gb, us, pl)."),
    page: int = Query(1, ge=1, description="Page number for pagination."),
):
    result = get_jobs_from_adzuna(
        what=what, location=location, country=country, page=page
    )
    return result


@app.get("/historical-salary/{country}")  # , dependencies=[Depends(verify_token)])
def get_historical_salary(
    country: str,
    location: Optional[str] = Query(None),
):
    historical_salary_data = get_historical_average_salary_data(country, location)
    return historical_salary_data


@app.post("/auth/token")
def auth(secret: str = Query(...)):
    token, expires = token_manager.login(secret)
    return {"access_token": token, "expires_at": expires.isoformat()}


# MUSE API endpoints
# endpoint to fetch and store MUSE jobs from single page, for testing purposes
@app.get("/muse-jobs")  # , dependencies=[Depends(verify_token)])
def get_and_store_muse_jobs(page: int = Query(1)):
    jobs = get_jobs_from_muse(page, save_to_db=False)

    return {"count": len(jobs.get("results", []))}


# initial endpoint to fetch and store MUSE jobs
@app.get("/muse-jobs-historical")  # , dependencies=[Depends(verify_token)])
def get_and_store_muse_jobs_historical(
    page_start: int = Query(1), page_end: Optional[int] = Query(None)
):
    return get_and_store_muse_jobs_multipage(page_start=page_start, page_end=page_end)


# endpoint to clean MUSE jobs HTML and create or update cleaned collection in MongoDB
@app.get("/muse-jobs-clean-html")  # , dependencies=[Depends(verify_token)])
def clean_muse_jobs_html():
    return create_or_update_cleaned_collection()


# endpoint to enrich MUSE jobs with skills using simple enrichment method - pure python
@app.get("/enrich-muse-skills")  # , dependencies=[Depends(verify_token)])
def run_enrichment_simple():
    return execute_enrichment_simple()


# endpoint to index enriched via simple method jobs from MongoDB to Elasticsearch
@app.get("/muse-mongo-to-elasticsearch")  # , dependencies=[Depends(verify_token)])
def index_muse_jobs_to_elasticsearch():
    return index_mongo_to_elasticsearch()


# endpoint to enrich single category MUSE jobs with skills using Elasticsearch-based enrichment method
# by default set category is Unknown; for testing purposes category can be passed as a query parameter
@app.get("/elasticsearch-enrich-muse-skills")  # , dependencies=[Depends(verify_token)])
def run_enrichment_es():
    return execute_enrichment_es()


# endpoint to enrich all categories MUSE jobs with skills using Elasticsearch-based
# enrichment method
@app.get(
    "/elasticsearch-enrich-muse-skills-all-categories"
)  # , dependencies=[Depends(verify_token)])
def run_all_categories_enrichment_es():
    return enrich_es_all_categories()


# async version of the endpoint to enrich all categories MUSE jobs with skills
# using Elasticsearch-based enrichment method
@app.get("/elasticsearch-enrich-muse-skills-all-categories-async")
async def run_all_categories_enrichment_es_async():
    return await enrich_es_all_categories_async()


# endpoint to recreate the db in postgresql and load enriched MUSE jobs from MongoDB to PostgreSQL
# postgresql is used as a source of data for the Dash app
@app.get("/muse-load-to-postgresql")  # , dependencies=[Depends(verify_token)])
def trigger_job_migration():
    return load_muse_jobs_to_postgresql()


# endpoint to index MUSE jobs for Dash search field in Elasticsearch
@app.get(
    "/create-elasticsearch-final-search-index"
)  # , dependencies=[Depends(verify_token)])
def index_elasticsearch_final():
    return index_for_final_search()
