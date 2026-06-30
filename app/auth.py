import logging
from datetime import datetime, timezone
from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import APIKey
from app.redis_client import redis_client
from app.config import settings

logger = logging.getLogger("Auth")

async def get_api_key(request: Request, db: Session = Depends(get_db)) -> APIKey:
    """
    Dependency to validate API Key, check IP whitelisting, and enforce Redis rate limits.
    """
    # 1. Extract API Key from query params or headers
    api_key_str = request.query_params.get("api_key")
    if not api_key_str:
        api_key_str = request.headers.get("X-API-Key")
        
    if not api_key_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key is missing. Pass it via 'api_key' query parameter or 'X-API-Key' header."
        )

    # 2. Query database for API Key
    api_key = db.query(APIKey).filter(APIKey.key_value == api_key_str).first()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key."
        )

    if not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key has been revoked or deactivated."
        )

    # 3. Check Expiration
    if api_key.expires_at:
        # ensure timezone aware comparison
        expires_at = api_key.expires_at.replace(tzinfo=timezone.utc) if api_key.expires_at.tzinfo is None else api_key.expires_at
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API Key has expired."
            )

    # 4. Check IP Whitelist
    client_ip = request.client.host if request.client else "127.0.0.1"
    # Support reverse proxies
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    if api_key.ip_whitelist:
        whitelisted_ips = [ip.strip() for ip in api_key.ip_whitelist.split(",") if ip.strip()]
        if client_ip not in whitelisted_ips:
            logger.warning(f"Unauthorized access attempt from IP {client_ip} for API Key of {api_key.owner}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Unauthorized IP Address. Host '{client_ip}' is not whitelisted."
            )

    # 5. Enforce Rate Limiting & Daily Quota in Redis
    plan_name = api_key.plan.lower()
    plan_limits = settings.plans.get(plan_name)
    
    # Default fallback limits if plan name is invalid
    rate_limit_minute = plan_limits.rate_limit_per_minute if plan_limits else api_key.rate_limit_per_minute
    daily_quota = plan_limits.daily_quota if plan_limits else api_key.daily_quota

    # Redis keys
    minute_key = f"rate:minute:{api_key_str}"
    day_key = f"quota:day:{api_key_str}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"

    try:
        # Enforce Minute Rate Limit
        current_minute_requests = await redis_client.client.incr(minute_key)
        if current_minute_requests == 1:
            await redis_client.client.expire(minute_key, 60)
            
        if current_minute_requests > rate_limit_minute:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {rate_limit_minute} requests per minute."
            )

        # Enforce Daily Quota
        current_daily_requests = await redis_client.client.incr(day_key)
        if current_daily_requests == 1:
            await redis_client.client.expire(day_key, 86400)
            
        if current_daily_requests > daily_quota:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily request quota exceeded: {daily_quota} requests per day."
            )

        # Increment monthly usage in database occasionally
        await redis_client.client.incr(f"usage:monthly:{api_key_str}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error enforcing rate limiting in Redis: {e}")
        # Fail open for safety if Redis is down, but log warning
        pass

    return api_key
