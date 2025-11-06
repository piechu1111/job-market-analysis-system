import datetime
import random
from time import sleep
from typing import Any, Dict, Optional, List
import requests
from requests.adapters import HTTPAdapter
import atexit
from pymongo import MongoClient, errors as mongo_errors
from pymongo import UpdateOne

from config import MUSE_API_KEY, MUSE_API_URL, MONGO_URI, logger

# MongoDB setup

mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client["jobs_db"]
mongo_raw_muse_offers_collection = mongo_db["raw_muse_offers"]

# Create a unique index on 'id' to ensure uniqueness and improve upsert performance
try:
    mongo_raw_muse_offers_collection.create_index("id", unique=True)
except mongo_errors.OperationFailure as e:
    logger.error("Failed to create index on 'id': %s", e)


# HTTP Session (no built-in backoff, manual retry below)
def _build_session() -> requests.Session:
    """
    Build a simple persistent requests.Session with connection pooling.
    Retry logic is implemented manually.
    """
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Accept": "application/json"})
    return session


_HTTP_SESSION = _build_session()

atexit.register(_HTTP_SESSION.close)

REQUEST_TIMEOUT = (5, 15)  # (connect_timeout, read_timeout)
RATE_LIMIT_SLEEP_SECONDS = 8.0  # api limit is 500 requests/hour
JITTER_SECONDS = 0.4  # small jitter to avoid bursty patterns

RETRIES = 2  # additional retry attempts
RETRIABLE_STATUS = {429, 500, 502, 503, 504}


def _request_with_retries(
    sess: requests.Session,
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: tuple = REQUEST_TIMEOUT,
) -> requests.Response:
    """
    Perform an HTTP request with retries.
    Retries apply to connection errors and retriable status codes:
    429, 500, 502, 503, 504.
    """
    attempts = 1 + RETRIES
    last_exception: Optional[Exception] = None
    response: Optional[requests.Response] = None

    for attempt in range(1, attempts + 1):
        try:
            response = sess.request(method, url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last_exception = e
            logger.warning(
                "HTTP error on attempt %s/%s: %s %s | error=%s",
                attempt,
                attempts,
                method,
                url,
                e,
            )
            if attempt < attempts:
                sleep(RATE_LIMIT_SLEEP_SECONDS)
                continue
            raise  # reraise if no retries left

        # success case
        if 200 <= response.status_code < 300:
            return response

        # retryable status codes
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
            sleep(RATE_LIMIT_SLEEP_SECONDS)
            continue

        # non retryable or out of attempts
        return response

    # this should never happen, but ensures function always returns/raises
    if last_exception:
        raise last_exception
    assert response is not None
    return response


def get_jobs_from_muse(
    page: int = 1, save_to_db: bool = False, session: Optional[requests.Session] = None
) -> Dict[str, Any]:
    """
    Fetch job offers from the Muse API for a given page.
    Optionally saves results to MongoDB.
    Returns: {"ok": bool, "data": dict, "error": str (optional)}
    """
    sess = session or _HTTP_SESSION

    params = {
        "app_key": MUSE_API_KEY,
        "page": page,
        "descending": "true",
    }

    try:
        response = _request_with_retries(
            sess, "GET", MUSE_API_URL, params=params, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as e:
        logger.error("Request to Muse API failed after retries (page=%s): %s", page, e)
        return {"ok": False, "error": "Failed to fetch data from Muse API."}

    # non 2xx response after retries
    if not (200 <= response.status_code < 300):
        logger.error(
            "Muse API returned non-2xx (page=%s): %s %s",
            page,
            response.status_code,
            response.text[:300],
        )
        return {"ok": False, "error": f"Muse API returned HTTP {response.status_code}."}

    # Content-Type validation
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" not in content_type:
        logger.error("Unexpected response type (page=%s): %s", page, content_type)
        return {"ok": False, "error": "Unexpected response format."}

    # parse JSON safely
    try:
        data = response.json()
    except ValueError as e:
        logger.error(
            "Invalid JSON from Muse API (page=%s): %s; body[:300]=%r",
            page,
            e,
            response.text[:300],
        )
        return {"ok": False, "error": "Invalid JSON from API."}

    # Optional MongoDB persistence
    results = data.get("results")
    if save_to_db and isinstance(results, list):
        valid_items: List[dict] = []

        for item in results:
            if not isinstance(item, dict) or "id" not in item:
                logger.warning("Skipping invalid item on page %s: %r", page, item)
                continue

            item = dict(item)  # shallow copy
            item["source"] = "muse"
            item["fetched_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            item.pop("_id", None)
            valid_items.append(item)

        # bulk upsert to MongoDB
        if valid_items:
            operations = [
                UpdateOne({"id": item["id"]}, {"$set": item}, upsert=True)
                for item in valid_items
            ]
            try:
                # ordered=False, so update next items after potential fail of current
                mongo_raw_muse_offers_collection.bulk_write(operations, ordered=False)
            except Exception as e:
                logger.error("bulk_write failed on page %s: %s", page, e)

    return {"ok": True, "data": data}


def get_and_store_muse_jobs_multipage(
    page_start: int = 1, page_end: Optional[int] = None
) -> Dict[str, Any]:
    """
    Fetch multiple pages of Muse job data with client-side rate limiting.
    Returns success even on partial completion, including pages fetched.
    """
    logger.info(
        "muse-jobs-multipage called with page_start=%s, page_end=%s",
        page_start,
        page_end,
    )

    if page_end is None:
        page_end = page_start + 5

    if page_start < 1 or page_end < page_start:
        return {
            "ok": False,
            "error": "Invalid page range. Ensure page_start < page_end and both > 0.",
        }

    pages_fetched: List[int] = []

    for page in range(page_start, page_end):
        logger.info("Starting Muse API call for page: %s", page)
        result = get_jobs_from_muse(page, save_to_db=True, session=_HTTP_SESSION)

        if not result or result.get("ok") is not True:
            logger.warning("Error on page %s: %s", page, result)
            return {
                "ok": False,
                "error": f"Error on page {page}.",
                "pages_fetched": pages_fetched,
            }

        data = result["data"]
        results = (data or {}).get("results") or []

        if not isinstance(results, list):
            logger.warning(
                "Unexpected results type on page %s: %s", page, type(results)
            )
            return {
                "ok": False,
                "error": f"Unexpected results type on page {page}.",
                "pages_fetched": pages_fetched,
            }

        if not results:
            logger.info("No results on page %s. Stopping pagination.", page)
            break

        pages_fetched.append(page)
        logger.info("Fetched %s jobs from page %s.", len(results), page)

        # rate limiting + small jitter
        sleep(RATE_LIMIT_SLEEP_SECONDS + random.uniform(0, JITTER_SECONDS))

    if not pages_fetched:
        return {"ok": False, "error": "No jobs fetched."}

    return {
        "ok": True,
        "message": f"Fetched pages: {pages_fetched}",
        "pages_fetched": pages_fetched,
    }
