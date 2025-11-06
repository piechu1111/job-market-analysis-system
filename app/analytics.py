from fastapi import APIRouter, Depends
import pandas as pd
import psycopg2
from app.authentication import verify_token
from config import POSTGRES_URL

router = APIRouter(
    prefix="/analytics", tags=["analytics"], dependencies=[Depends(verify_token)]
)


# Jobs per day -------------------------------------------------------------
@router.get("/jobs-per-day")
def jobs_per_day():
    sql = """
        SELECT publication_date::date AS day, COUNT(*) AS jobs
        FROM jobs
        WHERE publication_date > '2025-05-01'
        GROUP BY 1
        ORDER BY 1;
    """
    with psycopg2.connect(POSTGRES_URL) as conn:
        df = pd.read_sql(sql, conn)
    return df.to_dict(orient="list")


# Jobs by category ---------------------------------------------------------
@router.get("/jobs-by-category")
def jobs_by_category(limit: int = 40):
    sql = f"""
        SELECT c.name AS category, COUNT(*) AS jobs
        FROM job_categories jc
        JOIN categories c   ON c.id = jc.category_id
        WHERE c.name <> 'Unknown'
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT {limit};
    """
    with psycopg2.connect(POSTGRES_URL) as conn:
        df = pd.read_sql(sql, conn)
    return df.to_dict(orient="list")


# Jobs by location ---------------------------------------------------------
@router.get("/jobs-by-location")
def jobs_by_location(limit: int = 50):
    sql = f"""
        SELECT l.name AS location, COUNT(*) AS jobs
        FROM job_locations jl
        JOIN locations l ON l.id = jl.location_id
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT {limit};
    """
    with psycopg2.connect(POSTGRES_URL) as conn:
        df = pd.read_sql(sql, conn)
    return df.to_dict(orient="list")


# Top companies ------------------------------------------------------------
@router.get("/top-companies")
def top_companies(limit: int = 10):
    sql = f"""
        SELECT co.name AS company, COUNT(*) AS jobs
        FROM jobs j
        JOIN companies co ON co.id = j.company_id
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT {limit};
    """
    with psycopg2.connect(POSTGRES_URL) as conn:
        df = pd.read_sql(sql, conn)
    return df.to_dict(orient="list")


# Top skills per category --------------------------------------------------
@router.get("/top-skills")
def top_skills_per_category():
    sql = """
        SELECT skill, category, count FROM (
            SELECT
                s.name AS skill,
                s.category AS category,
                COUNT(*) AS count,
                RANK() OVER (PARTITION BY s.category ORDER BY COUNT(*) DESC) AS rnk
            FROM job_skills js
            JOIN skills s ON s.id = js.skill_id
            GROUP BY s.name, s.category
        ) ranked
        WHERE rnk <= 10
        ORDER BY category, count DESC;
    """
    with psycopg2.connect(POSTGRES_URL) as conn:
        df = pd.read_sql(sql, conn)
    return df.to_dict(orient="list")
