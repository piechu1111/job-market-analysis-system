from unittest.mock import patch, MagicMock
from app.dash_app import fetch_json


@patch("app.dash_app.token_client.get_token", return_value="Bearer fake-token")
@patch("app.dash_app.requests.get")
def test_fetch_json_search(mock_get, mock_get_token):
    # Prepare mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "title": "Senior Developer",
            "company": "TestCorp",
            "categories": ["Engineering"],
            "locations": ["Remote"],
            "skills": ["Python", "Django"],
            "highlight": {
                "name": ["<em>Developer</em>"],
                "cleaned_description": ["<mark>Full Stack</mark> developer needed"],
            },
            "url": "https://example.com/job/123",
        }
    ]
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    # Call function
    data = fetch_json("/search/offers", params={"q": "developer"})

    # Assertions
    assert isinstance(data, list)
    assert data[0]["title"] == "Senior Developer"
