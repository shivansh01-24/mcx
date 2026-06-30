import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from app.consensus import consensus_engine, latest_raw_feeds, last_validated_ticks
from app.config import settings

@pytest.fixture(autouse=True)
def mock_dependencies():
    """
    Automatically mocks PostgreSQL database sessions and Redis clients
    to isolate consensus calculations from network/infrastructure state.
    """
    with patch("app.consensus.SessionLocal") as mock_session_local, \
         patch("app.consensus.redis_client") as mock_redis:
         
        # 1. Setup Mock Database session
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        
        # Make DB queries return None by default (fast fallbacks)
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        # 2. Setup Mock Redis async methods
        async def mock_async_true(*args, **kwargs):
            return True
        async def mock_async_none(*args, **kwargs):
            return None
        async def mock_async_int(*args, **kwargs):
            return 1
            
        mock_redis.set_ltp = mock_async_true
        mock_redis.publish_tick = mock_async_int
        mock_redis.get_ltp = mock_async_none
        
        yield

@pytest.mark.asyncio
async def test_consensus_outlier_rejection():
    # Reset in-memory feeds
    latest_raw_feeds["gold"] = {}
    latest_raw_feeds["silver"] = {}
    last_validated_ticks["gold"] = None
    last_validated_ticks["silver"] = None

    now = datetime.now(timezone.utc)
    
    # We will mock 3 raw feeds:
    # 1. Source A: 100000.0 (near median)
    # 2. Source B: 100200.0 (near median)
    # 3. Source C: 105000.0 (deviates by 5% -> OUTLIER)
    
    # 1. Feed A
    await consensus_engine.handle_raw_tick({
        "commodity": "gold",
        "price": 100000.0,
        "source": "source_a",
        "timestamp": now,
        "latency_ms": 100,
        "raw_payload": "raw"
    })
    
    # 2. Feed B
    await consensus_engine.handle_raw_tick({
        "commodity": "gold",
        "price": 100200.0,
        "source": "source_b",
        "timestamp": now,
        "latency_ms": 110,
        "raw_payload": "raw"
    })
    
    # 3. Feed C (Outlier)
    await consensus_engine.handle_raw_tick({
        "commodity": "gold",
        "price": 105000.0,
        "source": "source_c",
        "timestamp": now,
        "latency_ms": 120,
        "raw_payload": "raw"
    })

    # Assert consensus committed value
    # The consensus price should be the average of Source A and Source B (outlier Source C rejected)
    # Average(100000, 100200) = 100100.0
    gold_validated = last_validated_ticks["gold"]
    assert gold_validated is not None
    assert gold_validated["price"] == 100100.0
    assert gold_validated["source_count"] == 2 # Only Source A and Source B included
    assert gold_validated["estimated"] is False
    assert gold_validated["stale"] is False

@pytest.mark.asyncio
async def test_consensus_failsafe_fallback():
    # Reset in-memory feeds
    latest_raw_feeds["gold"] = {}
    latest_raw_feeds["silver"] = {}
    last_validated_ticks["gold"] = None

    # Seed a last verified tick in memory
    last_validated_ticks["gold"] = {
        "commodity": "gold",
        "price": 100100.0,
        "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "collector": "source_a",
        "collector_latency_ms": 100,
        "confidence": 99.0,
        "source_count": 2,
        "estimated": False,
        "stale": False
    }

    # Trigger fail-safe directly
    await consensus_engine.trigger_failsafe("gold", "Testing fail-safe trigger")
    
    # Fetch cached LTP
    # The tick should now be marked as stale = True
    gold_validated = last_validated_ticks["gold"]
    assert gold_validated is not None
    assert gold_validated["stale"] is True
    assert gold_validated["estimated"] is False
    assert gold_validated["confidence"] == 0.0
    assert gold_validated["source_count"] == 0
