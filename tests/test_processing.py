import pytest
from unittest.mock import patch
from app.muse_processing import index_for_final_search


@pytest.fixture
def fake_docs():
    return [
        {
            "_id": "abc123",
            "name": "Test Job",
            "cleaned_description": "Python developer",
            "publication_date": "2024-01-01",
            "refs": {"landing_page": "http://example.com/job/abc123"},
            "company": {"name": "TestCo", "short_name": "TC"},
            "categories": [{"name": "Software Engineering"}],
            "locations": [{"name": "Remote"}],
            "merged_skills": ["Python", "Django"],
        }
    ]


@patch("app.muse_processing.bulk")
@patch("app.muse_processing.logger")
@patch("app.muse_processing.enriched_muse_offers_collection")
@patch("app.muse_processing.es")
def test_index_for_final_search_creates_and_indexes(
    mock_es, mock_collection, mock_logger, mock_bulk, fake_docs
):
    # Setup mocks
    mock_es.indices.exists.return_value = True
    mock_collection.find.return_value = fake_docs
    mock_es.indices.create.return_value = None
    mock_es.indices.delete.return_value = None
    mock_bulk.return_value = None

    # Run function
    index_for_final_search(index_name="test_index", drop_if_exists=True)

    # Check index deletion and creation
    mock_es.indices.exists.assert_called_with(index="test_index")
    mock_es.indices.delete.assert_called_with(index="test_index")
    mock_es.indices.create.assert_called()
    # Check bulk called with correct doc
    args, kwargs = mock_bulk.call_args
    actions = args[1]
    assert actions[0]["_id"] == "abc123"
    assert actions[0]["_index"] == "test_index"
    assert actions[0]["_source"]["id"] == "abc123"
    assert actions[0]["_source"]["url"] == "http://example.com/job/abc123"
    # Logger called
    assert mock_logger.info.call_count >= 2


@patch("app.muse_processing.bulk")
@patch("app.muse_processing.logger")
@patch("app.muse_processing.enriched_muse_offers_collection")
@patch("app.muse_processing.es")
def test_index_for_final_search_no_docs(
    mock_es, mock_collection, mock_logger, mock_bulk
):
    mock_es.indices.exists.return_value = False
    mock_collection.find.return_value = []
    mock_es.indices.create.return_value = None

    index_for_final_search(index_name="empty_index", drop_if_exists=True)

    # Should not call bulk if no docs
    assert not mock_bulk.called


@patch("app.muse_processing.bulk")
@patch("app.muse_processing.logger")
@patch("app.muse_processing.enriched_muse_offers_collection")
@patch("app.muse_processing.es")
def test_index_for_final_search_no_drop(
    mock_es, mock_collection, mock_logger, mock_bulk, fake_docs
):
    mock_es.indices.exists.return_value = False
    mock_collection.find.return_value = fake_docs
    mock_es.indices.create.return_value = None

    index_for_final_search(index_name="no_drop_index", drop_if_exists=False)

    # Should not call delete
    assert not mock_es.indices.delete.called
    # Should create index and call bulk
    assert mock_es.indices.create.called
    assert mock_bulk.called
