import os
import shutil
import pytest
import asyncio
from unittest.mock import MagicMock, patch
from app.collector_manager import collector_manager
from app.collectors.base import BaseCollector

COLLECTORS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "collectors")

# Define a mock collector class to write out for testing hot loading
MOCK_COLLECTOR_CODE = """
from app.collectors.base import BaseCollector
import time

class MockPluginCollector(BaseCollector):
    MANIFEST = {
        "name": "mock_plugin",
        "version": "1.0.0",
        "priority": 1,
        "exchange": "MCX",
        "supported_commodities": ["gold"],
        "polling_interval": 1,
        "collector_type": "REST",
        "required_env": [],
        "author": "Mock Author",
        "min_platform_version": "1.0.0"
    }

    async def collect(self):
        return {
            "gold": {
                "price": 99999.9,
                "raw_payload": "mock"
            }
        }
"""

@pytest.fixture(autouse=True)
def mock_collector_manager_deps():
    """
    Mock database sessions and redis client in collector manager.
    """
    with patch("app.collector_manager.SessionLocal") as mock_session_local, \
         patch("app.collector_manager.event_bus") as mock_event_bus:
         
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        
        # Mock database queries for collector metrics
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        # Mock event bus publishes
        async def mock_publish(*args, **kwargs):
            return True
        mock_event_bus.publish_raw_tick = mock_publish
        
        yield

@pytest.mark.asyncio
async def test_dynamic_collector_discovery():
    test_file_path = os.path.join(COLLECTORS_DIR, "mock_plugin.py")
    
    # Clean up if exists
    if os.path.exists(test_file_path):
        os.remove(test_file_path)

    # Make sure manager is running
    await collector_manager.start()
    
    try:
        # Assert mock is NOT loaded initially
        assert "mock_plugin" not in collector_manager.collectors

        # Write mock plugin code to collectors directory
        with open(test_file_path, "w") as f:
            f.write(MOCK_COLLECTOR_CODE)

        # Trigger manual scan of files (to avoid waiting 5 seconds of reloader loop)
        await collector_manager.scan_collectors_dir()

        # Assert mock is now loaded and registered
        assert "mock_plugin" in collector_manager.collectors
        
        # Verify Manifest
        col = collector_manager.get_collector("mock_plugin")
        assert col.MANIFEST["name"] == "mock_plugin"
        assert col.MANIFEST["author"] == "Mock Author"

    finally:
        # Clean up
        if os.path.exists(test_file_path):
            os.remove(test_file_path)
        # Unload
        await collector_manager.unload_collector("mock_plugin")
        await collector_manager.stop()
