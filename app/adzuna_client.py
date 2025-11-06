# this API is not integrated into Mongo/Dash,
# it is for potential extention of the project in future
import random
from time import sleep
from typing import Any, Dict, Optional
import atexit
import requests
from requests.adapters import HTTPAdapter
from config import ADZUNA_APP_ID, ADZUNA_APP_KEY, logger


def _build_session() -> requests.Session:
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Accept": "application/json"})
    return session


_HTTP_SESSION = _build_session()
atexit.register(_HTTP_SESSION.close)

REQUEST_TIMEOUT = (5, 15)  # (connect_timeout, read_timeout)
RATE_LIMIT_SLEEP_SECONDS = 5.0
JITTER_SECONDS = 0.3
RETRIES = 2
RETRIABLE_STATUS = {429, 500, 502, 503, 504}


def _request_with_retries(
    sess: requests.Session,
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: tuple = REQUEST_TIMEOUT,
) -> requests.Response:
    attempts = 1 + RETRIES
    last_exc: Optional[Exception] = None
    response: Optional[requests.Response] = None

    for attempt in range(1, attempts + 1):
        try:
            response = sess.request(method, url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            logger.warning(
                "HTTP error on attempt %s/%s: %s %s | error=%s",
                attempt,
                attempts,
                method,
                url,
                e,
            )
            if attempt < attempts:
                sleep(RATE_LIMIT_SLEEP_SECONDS + random.uniform(0, JITTER_SECONDS))
                continue
            raise

        if 200 <= response.status_code < 300:
            return response

        if response.status_code in RETRIABLE_STATUS and attempt < attempts:
            logger.warning(
                "Retryable status %s on attempt %s/%s: %s %s | body[:200]=%r",
                response.status_code,
                attempt,
                attempts,
                method,
                url,
                response.text[:200],
            )
            sleep(RATE_LIMIT_SLEEP_SECONDS + random.uniform(0, JITTER_SECONDS))
            continue

        return response

    if last_exc:
        raise last_exc
    assert response is not None
    return response


# Adzuna API calls
def get_jobs_from_adzuna(
    what: str,
    location: str,
    country: str = "gb",
    page: int = 1,
    results_per_page: int = 5,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Pobiera oferty pracy z Adzuna API.
    Zwraca słownik {"ok": bool, "data": dict, "error": str (opcjonalnie)}.
    """
    sess = session or _HTTP_SESSION
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "what": what,
        "where": location,
        "results_per_page": results_per_page,
    }

    try:
        response = _request_with_retries(sess, "GET", url, params=params)
    except requests.RequestException as e:
        logger.error("Adzuna request failed (page=%s): %s", page, e)
        return {"ok": False, "error": "Failed to fetch data from Adzuna API."}

    if not (200 <= response.status_code < 300):
        logger.error(
            "Adzuna non-2xx (page=%s): %s %s",
            page,
            response.status_code,
            response.text[:300],
        )
        return {
            "ok": False,
            "error": f"Adzuna API returned HTTP {response.status_code}.",
        }

    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" not in content_type:
        logger.error("Unexpected Content-Type from Adzuna: %s", content_type)
        return {"ok": False, "error": "Unexpected response format."}

    try:
        data = response.json()
    except ValueError as e:
        logger.error(
            "Invalid JSON from Adzuna: %s; body[:200]=%r", e, response.text[:200]
        )
        return {"ok": False, "error": "Invalid JSON from API."}

    return {"ok": True, "data": data}


def get_historical_average_salary_data(
    country: str,
    location: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Pobiera dane historyczne średnich zarobków z Adzuna /history.
    """
    sess = session or _HTTP_SESSION
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/history"

    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
    }

    if location:
        parts = [p.strip() for p in location.split(",") if p.strip()]
        for i, part in enumerate(parts):
            params[f"location{i}"] = part

    try:
        response = _request_with_retries(sess, "GET", url, params=params)
    except requests.RequestException as e:
        logger.error("Adzuna /history request failed: %s", e)
        return {
            "ok": False,
            "error": "Failed to fetch historical data from Adzuna API.",
        }

    if not (200 <= response.status_code < 300):
        logger.error(
            "Adzuna /history non-2xx: %s %s", response.status_code, response.text[:300]
        )
        return {
            "ok": False,
            "error": f"Adzuna API returned HTTP {response.status_code}.",
        }

    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" not in content_type:
        logger.error("Unexpected Content-Type from Adzuna /history: %s", content_type)
        return {"ok": False, "error": "Unexpected response format."}

    try:
        data = response.json()
    except ValueError as e:
        logger.error(
            "Invalid JSON from Adzuna /history: %s; body[:200]=%r",
            e,
            response.text[:200],
        )
        return {"ok": False, "error": "Invalid JSON from API."}

    return {"ok": True, "data": data}
