from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime
import requests
from dotenv import load_dotenv
import os
import logging

load_dotenv()

API_BASE = os.getenv("API_URL")
SECRET = os.getenv("AUTH_SECRET")

if not API_BASE:
    raise ValueError("Missing API_URL in environment variables.")
if not SECRET:
    raise ValueError("Missing AUTH_SECRET in environment variables.")

logger = logging.getLogger(__name__)


def get_token(force_refresh=True):
    if force_refresh:
        res = requests.post(f"{API_BASE}/auth/token", params={"secret": SECRET})
        res.raise_for_status()
        return res.json()["access_token"]


def call_api(path: str, params=None):
    try:
        token = get_token()
        headers = {"Authorization": f"Bearer {token}"}
        res = requests.get(f"{API_BASE}{path}", headers=headers, params=params)

        if res.status_code == 401:
            logger.warning(
                f"Token expired or invalid, refreshing token and retrying {path}"
            )
            token = get_token(force_refresh=True)
            headers = {"Authorization": f"Bearer {token}"}
            res = requests.get(f"{API_BASE}{path}", headers=headers, params=params)

        res.raise_for_status()
        logger.info(f"Response from {path}: {res.text}")
        return res.json()

    except Exception as e:
        logger.error(f"Error calling {path}: {e}")
        raise


default_args = {"start_date": datetime(2025, 1, 1)}

with DAG(
    dag_id="job_api_pipeline_full",
    default_args=default_args,
    catchup=False,
    schedule_interval="52 8 * * *",  # every day at 09:05 utc
    tags=["jobapi"],
) as dag:

    fetch_jobs = PythonOperator(
        task_id="fetch_jobs_historical",
        python_callable=lambda: call_api(
            "/muse-jobs-historical", {"page_start": 1, "page_end": 100}
        ),
    )

    clean_html = PythonOperator(
        task_id="clean_html", python_callable=lambda: call_api("/muse-jobs-clean-html")
    )

    enrich_simple = PythonOperator(
        task_id="enrich_skills_simple",
        python_callable=lambda: call_api("/enrich-muse-skills"),
    )

    index_simple = PythonOperator(
        task_id="index_to_elasticsearch",
        python_callable=lambda: call_api("/muse-mongo-to-elasticsearch"),
    )

    enrich_es = PythonOperator(
        task_id="enrich_skills_es",
        python_callable=lambda: call_api("/elasticsearch-enrich-muse-skills"),
    )

    enrich_es_all = PythonOperator(
        task_id="enrich_skills_es_all_categories",
        python_callable=lambda: call_api(
            "/elasticsearch-enrich-muse-skills-all-categories-async"
        ),
    )

    load_to_postgres = PythonOperator(
        task_id="load_to_postgresql",
        python_callable=lambda: call_api("/muse-load-to-postgresql"),
    )

    index_final = PythonOperator(
        task_id="create_final_search_index",
        python_callable=lambda: call_api("/create-elasticsearch-final-search-index"),
    )

    # order the tasks
    (
        fetch_jobs
        >> clean_html
        >> enrich_simple
        >> index_simple
        >> enrich_es
        >> enrich_es_all
        >> load_to_postgres
        >> index_final
    )
