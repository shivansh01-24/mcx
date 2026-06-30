import os
import redis.asyncio as aioredis
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("Redis")

# -----------------------------------------------------------------------
# Priority resolution:
#   1. REDIS_URL or REDIS_PRIVATE_URL env var  (Railway injects these)
#   2. REDIS_HOST / REDIS_PORT env vars
#   3. config.yaml settings  (local / Docker Compose)
# -----------------------------------------------------------------------
def _resolve_redis_url() -> Optional[str]:
    return (
        os.environ.get("REDIS_URL") or
        os.environ.get("REDIS_PRIVATE_URL") or
        os.environ.get("REDISPRIVATE_URL")
    )


def _build_redis_client() -> aioredis.Redis:
    url = _resolve_redis_url()
    if url:
        logger.info("Redis connection type: Railway REDIS_URL")
        host_part = url.split("@")[-1].split("/")[0] if "@" in url else url
        logger.info(f"Redis host: {host_part}")
        return aioredis.Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )

    # Fall back to settings (config.yaml / MCX_REDIS__* env vars)
    from app.config import settings
    host = settings.redis.host
    port = settings.redis.port
    db = settings.redis.db

    if host in ("redis",):
        conn_type = "Local Docker Compose"
    else:
        conn_type = "Local Bare-Metal"
    logger.info(f"Redis connection type: {conn_type}")
    logger.info(f"Redis host: {host}:{port}")

    return aioredis.Redis(
        host=host,
        port=port,
        db=db,
        decode_responses=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
    )


class RedisClient:
    def __init__(self):
        self.client = _build_redis_client()

    async def ping(self) -> bool:
        try:
            return await self.client.ping()
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False

    async def get_ltp(self, commodity: str) -> Optional[Dict[str, Any]]:
        """Retrieves the latest cached validated tick from Redis."""
        try:
            val = await self.client.get(f"ltp:{commodity.lower()}")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"Error getting ltp for {commodity} from Redis: {e}")
        return None

    async def set_ltp(self, commodity: str, data: Dict[str, Any], ttl: int = None) -> bool:
        """Caches the latest validated tick in Redis."""
        if ttl is None:
            from app.config import settings
            ttl = settings.redis.ttl_seconds
        try:
            key = f"ltp:{commodity.lower()}"
            await self.client.set(key, json.dumps(data), ex=ttl)
            return True
        except Exception as e:
            logger.error(f"Error setting ltp for {commodity} in Redis: {e}")
            return False

    async def publish_tick(self, commodity: str, tick_data: Dict[str, Any]) -> int:
        """Publishes price ticks to Redis Pub/Sub channels."""
        try:
            payload = json.dumps(tick_data)
            r1 = await self.client.publish(f"market:updates:{commodity.lower()}", payload)
            r2 = await self.client.publish("market:updates:all", payload)
            return r1 + r2
        except Exception as e:
            logger.error(f"Error publishing tick to Redis: {e}")
            return 0

    def get_pubsub(self):
        """Returns a new async PubSub object."""
        return self.client.pubsub()

    async def get_keys_count(self) -> int:
        """Counts keys in Redis using SCAN."""
        try:
            count = 0
            async for _ in self.client.scan_iter("*"):
                count += 1
            return count
        except:
            return 0


redis_client = RedisClient()
