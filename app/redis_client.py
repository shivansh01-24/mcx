import redis.asyncio as aioredis
import json
import logging
from app.config import settings
from typing import Optional, Dict, Any

logger = logging.getLogger("Redis")

class RedisClient:
    def __init__(self):
        # Establish connection pool using aioredis
        self.client = aioredis.Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            db=settings.redis.db,
            decode_responses=True,
            socket_timeout=2.0
        )

    async def ping(self) -> bool:
        try:
            return await self.client.ping()
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False

    async def get_ltp(self, commodity: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the latest cached validated tick from Redis asynchronously.
        """
        try:
            val = await self.client.get(f"ltp:{commodity.lower()}")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"Error getting ltp for {commodity} from Redis: {e}")
        return None

    async def set_ltp(self, commodity: str, data: Dict[str, Any], ttl: int = None) -> bool:
        """
        Caches the latest validated tick in Redis asynchronously.
        """
        if ttl is None:
            ttl = settings.redis.ttl_seconds
        try:
            key = f"ltp:{commodity.lower()}"
            await self.client.set(key, json.dumps(data), ex=ttl)
            return True
        except Exception as e:
            logger.error(f"Error setting ltp for {commodity} in Redis: {e}")
            return False

    async def publish_tick(self, commodity: str, tick_data: Dict[str, Any]) -> int:
        """
        Publishes price ticks to Redis Pub/Sub channels asynchronously.
        """
        try:
            payload = json.dumps(tick_data)
            receivers1 = await self.client.publish(f"market:updates:{commodity.lower()}", payload)
            receivers2 = await self.client.publish("market:updates:all", payload)
            return receivers1 + receivers2
        except Exception as e:
            logger.error(f"Error publishing tick to Redis: {e}")
            return 0

    def get_pubsub(self):
        """
        Returns a new async PubSub object.
        """
        return self.client.pubsub()

    async def get_keys_count(self) -> int:
        """
        Counts keys in Redis asynchronously using SCAN to avoid blocking the event loop.
        """
        try:
            count = 0
            async for _ in self.client.scan_iter("*"):
                count += 1
            return count
        except:
            return 0

redis_client = RedisClient()
