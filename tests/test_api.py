import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from app.main import app
from app.auth import get_api_key
from app.database import get_db
from app.models import APIKey

client = TestClient(app)

@pytest.fixture(autouse=True)
def override_dependencies():
    """
    Overrides get_api_key and get_db dependencies globally for the test suite.
    """
    mock_key = APIKey(
        key_value="test_key",
        plan="unlimited",
        rate_limit_per_minute=10000,
        daily_quota=9999,
        owner="Test Developer",
        is_active=True
    )
    mock_db = MagicMock()
    
    # Configure mock DB queries to return mock values
    mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []
    
    # Apply dependency overrides
    app.dependency_overrides[get_api_key] = lambda: mock_key
    app.dependency_overrides[get_db] = lambda: mock_db
    
    # Patch startup events to prevent connecting to non-existent redis/db hosts during TestClient context
    with patch("app.main.redis_client.ping") as mock_ping, \
         patch("app.main.init_db") as mock_init, \
         patch("app.main.warm_redis_cache") as mock_warm:
        
        async def mock_async_true():
            return True
        mock_ping.return_value = mock_async_true()
        
        yield mock_db
        
    app.dependency_overrides.clear()

@patch("app.main.redis_client")
def test_get_prices_route_success(mock_redis):
    # Mock Redis return value for Gold and Silver
    async def mock_ltp(comm):
        return {
            "commodity": comm,
            "price": 100250.0 if comm == "gold" else 75100.0,
            "change": 120.0,
            "change_percent": 0.12,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "collector": "mcxlive",
            "collector_latency_ms": 35,
            "confidence": 99.5,
            "source_count": 2,
            "estimated": False,
            "stale": False
        }
    mock_redis.get_ltp = mock_ltp

    res = client.get("/api/v1/prices")
    assert res.status_code == 200
    
    body = res.json()
    assert body["success"] is True
    assert "timestamp" in body
    assert "latency_ms" in body
    assert "request_id" in body
    
    data = body["data"]
    assert len(data) == 2
    assert data[0]["commodity"] == "gold"
    assert data[0]["price"] == 100250.0
    assert data[1]["commodity"] == "silver"
    assert data[1]["price"] == 75100.0

@patch("app.main.redis_client")
def test_get_gold_price_route(mock_redis):
    async def mock_ltp(comm):
        return {
            "commodity": "gold",
            "price": 100250.0,
            "change": 120.0,
            "change_percent": 0.12,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "collector": "mcxlive",
            "collector_latency_ms": 35,
            "confidence": 99.5,
            "source_count": 2,
            "estimated": False,
            "stale": False
        }
    mock_redis.get_ltp = mock_ltp

    res = client.get("/api/v1/gold")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"]["commodity"] == "gold"
    assert body["data"]["price"] == 100250.0

def test_get_history_empty_success():
    res = client.get("/api/v1/history/gold?interval=5m")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"] == []
