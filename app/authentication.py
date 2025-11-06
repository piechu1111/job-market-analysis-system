import uuid
import psycopg2
import requests
from typing import Optional
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Header
from psycopg2.extras import RealDictCursor
from config import AUTH_SECRET, POSTGRES_URL

EXPIRES_MINUTES = 30


def save_token_to_db(token: str, expires_at: datetime):
    with psycopg2.connect(POSTGRES_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    id SERIAL PRIMARY KEY,
                    token TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """
            )
            cur.execute("DELETE FROM auth_tokens")
            cur.execute(
                "INSERT INTO auth_tokens (token, expires_at) VALUES (%s, %s)",
                (token, expires_at),
            )
            conn.commit()


def load_token_from_db() -> tuple[str, datetime]:
    with psycopg2.connect(POSTGRES_URL) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM auth_tokens LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="Token not found")
            return row["token"], row["expires_at"]


# server side
class TokenManager:
    def __init__(self, expires_in_minutes: int = 30):
        self.secret = AUTH_SECRET
        self.expires_in = expires_in_minutes

    def generate_token(self) -> tuple[str, datetime]:
        token = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.expires_in)
        save_token_to_db(token, expires_at)
        return token, expires_at

    def login(self, secret: str) -> tuple[str, datetime]:
        if secret != self.secret:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        try:
            token, expires_at = load_token_from_db()
            if datetime.now(timezone.utc) < expires_at:
                return token, expires_at
        except Exception:
            pass  # token not found or expired

        return self.generate_token()

    def verify_token(self, authorization: Optional[str] = Header(None)) -> bool:
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing token")

        token, expires_at = load_token_from_db()

        if authorization != token:
            raise HTTPException(status_code=401, detail="Invalid token")
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=401, detail="Token expired")

        return True


# client side
class TokenClient:
    def __init__(self, api_url: str):
        self.secret = AUTH_SECRET
        if not self.secret:
            raise RuntimeError("AUTH_SECRET is not set in environment variables")

        self.api_url = api_url
        self.token = None
        self.expires_at = None

    def get_token(self, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self.token
            and self.expires_at
            and datetime.now(timezone.utc) < self.expires_at
        ):
            return self.token

        response = requests.post(
            f"{self.api_url}/auth/token", params={"secret": self.secret}
        )
        response.raise_for_status()
        data = response.json()

        self.token = data["access_token"]
        self.expires_at = datetime.fromisoformat(data["expires_at"])
        return self.token


# singletons
token_manager = TokenManager(expires_in_minutes=90)
verify_token = token_manager.verify_token
