from fastapi.testclient import TestClient
from app.fast_api_main import app
from config import AUTH_SECRET

client = TestClient(app)


def test_auth_token_success():
    response = client.post(f"/auth/token?secret={AUTH_SECRET}")
    assert response.status_code == 200
    json_data = response.json()
    assert "access_token" in json_data
    assert "expires_at" in json_data
