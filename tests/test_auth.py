import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from fastapi import HTTPException, status
from app.auth import get_api_key
from app.models import APIKey

class MockRequest:
    def __init__(self, query_params=None, headers=None, client_host="127.0.0.1"):
        self.query_params = query_params or {}
        self.headers = headers or {}
        # Client host structure
        class Client:
            def __init__(self, host):
                self.host = host
        self.client = Client(client_host)

@pytest.fixture
def mock_db_session():
    return MagicMock()

@pytest.mark.asyncio
async def test_auth_missing_api_key(mock_db_session):
    req = MockRequest()
    with pytest.raises(HTTPException) as exc:
        await get_api_key(req, mock_db_session)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "API Key is missing" in exc.value.detail

@pytest.mark.asyncio
async def test_auth_invalid_api_key(mock_db_session):
    req = MockRequest(query_params={"api_key": "invalid_key"})
    mock_db_session.query.return_value.filter.return_value.first.return_value = None
    
    with pytest.raises(HTTPException) as exc:
        await get_api_key(req, mock_db_session)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Invalid API Key" in exc.value.detail

@pytest.mark.asyncio
async def test_auth_deactivated_key(mock_db_session):
    req = MockRequest(query_params={"api_key": "deactivated_key"})
    mock_key = APIKey(key_value="deactivated_key", is_active=False, owner="Test User")
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_key

    with pytest.raises(HTTPException) as exc:
        await get_api_key(req, mock_db_session)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert "revoked or deactivated" in exc.value.detail

@pytest.mark.asyncio
async def test_auth_expired_key(mock_db_session):
    req = MockRequest(query_params={"api_key": "expired_key"})
    expired_time = datetime.now(timezone.utc) - timedelta(days=1)
    mock_key = APIKey(key_value="expired_key", is_active=True, expires_at=expired_time, owner="Test User")
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_key

    with pytest.raises(HTTPException) as exc:
        await get_api_key(req, mock_db_session)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert "expired" in exc.value.detail

@pytest.mark.asyncio
async def test_auth_ip_whitelisting(mock_db_session):
    req = MockRequest(query_params={"api_key": "whitelisted_key"}, client_host="192.168.1.5")
    mock_key = APIKey(
        key_value="whitelisted_key",
        is_active=True,
        owner="Test User",
        ip_whitelist="192.168.1.1, 192.168.1.2"  # client host is 192.168.1.5 -> BLOCKED
    )
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_key

    with pytest.raises(HTTPException) as exc:
        await get_api_key(req, mock_db_session)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert "Unauthorized IP Address" in exc.value.detail

@pytest.mark.asyncio
@patch("app.auth.redis_client")
async def test_auth_rate_limiting(mock_redis, mock_db_session):
    req = MockRequest(query_params={"api_key": "test_key"})
    mock_key = APIKey(
        key_value="test_key",
        is_active=True,
        plan="free",
        rate_limit_per_minute=60,
        daily_quota=5000,
        owner="Test User"
    )
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_key

    # Mock Redis responses
    async def mock_incr(*args, **kwargs):
        return 61 # Exceeds minute rate limit of 60
    mock_redis.client.incr = mock_incr

    with pytest.raises(HTTPException) as exc:
        await get_api_key(req, mock_db_session)
    assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert "Rate limit exceeded" in exc.value.detail
