import pytest
import asyncio
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from app.event_bus import event_bus
from app.models import RawTick

@pytest.fixture(autouse=True)
def mock_db_in_event_bus():
    with patch("app.event_bus.SessionLocal") as mock_session_local:
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        yield mock_db

@pytest.mark.asyncio
async def test_event_bus_publishing_and_workers(mock_db_in_event_bus):
    # Setup consensus callback mock
    callback_called = asyncio.Event()
    received_event = {}

    async def mock_callback(event):
        nonlocal received_event
        received_event = event
        callback_called.set()

    event_bus.register_consensus_callback(mock_callback)
    
    # Start EventBus workers
    await event_bus.start_workers()

    try:
        # Publish a raw tick
        await event_bus.publish_raw_tick(
            commodity="gold",
            price=100250.00,
            source="test_source",
            latency_ms=45,
            raw_payload="{'val': 100250}"
        )

        # Wait for consensus callback to be executed
        await asyncio.wait_for(callback_called.wait(), timeout=3.0)

        # Assert callback received exact attributes
        assert received_event["commodity"] == "gold"
        assert received_event["price"] == 100250.00
        assert received_event["source"] == "test_source"
        assert received_event["latency_ms"] == 45
        assert received_event["raw_payload"] == "{'val': 100250}"

        # Assert PostgreSQL save called
        # The save occurs asynchronously in a thread pool, wait briefly
        await asyncio.sleep(0.5)
        assert mock_db_in_event_bus.add.called
        assert mock_db_in_event_bus.commit.called

    finally:
        # Stop workers
        await event_bus.stop_workers()
