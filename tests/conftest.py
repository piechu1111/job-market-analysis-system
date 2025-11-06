import sys
from unittest.mock import MagicMock

# create a MagicMock to stand in for the mongo collection
mock_collection = MagicMock()
mock_collection.create_index.return_value = None

# create a MagicMock module for app.muse_client
mock_muse_client = MagicMock()
mock_muse_client.mongo_raw_muse_offers_collection = mock_collection

# inject the mock module into sys.modules before tests run
sys.modules["app.muse_client"] = mock_muse_client
