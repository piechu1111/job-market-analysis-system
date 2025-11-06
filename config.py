import os
import sys
from dotenv import load_dotenv
from logging_config import setup_logging

# common logger setup for whole system
logger = setup_logging()

load_dotenv()


def get_env_var(name: str) -> str:
    # get env var or exit if missing
    value = os.getenv(name)
    if not value:
        logger.error(f"Missing required env var: {name}")
        sys.exit(1)
    return value


# internal
API_URL = get_env_var("API_URL")
AUTH_SECRET = get_env_var("AUTH_SECRET")
MONGO_URI = get_env_var("MONGO_URI")
POSTGRES_URL = get_env_var("POSTGRES_URL")
ES_URL = get_env_var("ES_URL")
DASH_URL = get_env_var("DASH_URL")

# external
MUSE_API_KEY = get_env_var("MUSE_API_KEY")
MUSE_API_URL = get_env_var("MUSE_API_URL")
ADZUNA_APP_ID = get_env_var("ADZUNA_APP_ID")
ADZUNA_APP_KEY = get_env_var("ADZUNA_APP_KEY")
