import logging
import asyncio
from typing import Dict, Any, List, Optional
import statistics
from datetime import datetime, timezone, timedelta
from app.config import settings
from app.database import SessionLocal
from app.models import ValidatedTick
from app.redis_client import redis_client

logger = logging.getLogger("Consensus")

# In-memory store for the latest ticks from each collector
# Format: { commodity: { collector_name: { "price": float, "timestamp": datetime, "latency_ms": int } } }
latest_raw_feeds: Dict[str, Dict[str, Dict[str, Any]]] = {
    "gold": {},
    "silver": {}
}

# In-memory store for the last successfully validated tick for fail-safe fallback
last_validated_ticks: Dict[str, Dict[str, Any]] = {
    "gold": None,
    "silver": None
}

class ConsensusEngine:
    def __init__(self, collector_manager=None):
        self.collector_manager = collector_manager
        self.locks = {
            "gold": asyncio.Lock(),
            "silver": asyncio.Lock()
        }

    def set_collector_manager(self, manager):
        self.collector_manager = manager

    async def handle_raw_tick(self, event: Dict[str, Any]):
        """
        Callback triggered by the EventBus when a new raw tick is successfully logged.
        """
        commodity = event["commodity"]
        source = event["source"]
        price = event["price"]
        timestamp = event["timestamp"]
        latency_ms = event["latency_ms"]

        if commodity not in latest_raw_feeds:
            # We only track configured commodities (gold and silver)
            return

        # Update in-memory feed store
        latest_raw_feeds[commodity][source] = {
            "price": price,
            "timestamp": timestamp,
            "latency_ms": latency_ms
        }

        # Trigger consensus evaluation for the commodity
        await self.evaluate_consensus(commodity)

    async def evaluate_consensus(self, commodity: str):
        """
        Aggregates available raw feeds, filters outliers, computes consensus price,
        and broadcasts updates. Protected by an asyncio.Lock per commodity to prevent race conditions.
        """
        async with self.locks[commodity]:
            now = datetime.now(timezone.utc)
            feeds = latest_raw_feeds[commodity]
            
            # 1. Filter out stale feeds (older than 2 minutes)
            active_feeds = {}
            for source, feed in list(feeds.items()):
                if source == "yfinance_proxy":
                    continue
                time_diff = (now - feed["timestamp"]).total_seconds()
                # If the feed is not stale and its collector is healthy
                is_healthy = True
                if self.collector_manager:
                    is_healthy = self.collector_manager.is_collector_healthy(source)
                    
                if time_diff <= 120 and is_healthy:
                    active_feeds[source] = feed

            # If zero active feeds from standard sources, trigger Fail-Safe Mode
            if not active_feeds:
                await self.trigger_failsafe(commodity, "No active live collectors available.")
                return

            # 2. Extract prices for statistical evaluation
            prices = [f["price"] for f in active_feeds.values()]
            sources = list(active_feeds.keys())

            # If only 1 source is active, consensus cannot reject outliers. We trust the single source but reduce confidence
            if len(prices) == 1:
                primary_source = sources[0]
                feed = active_feeds[primary_source]
                await self.commit_consensus(
                    commodity=commodity,
                    price=feed["price"],
                    collector=primary_source,
                    latency_ms=feed["latency_ms"],
                    confidence=50.0,  # Lower confidence for single source
                    source_count=1,
                    estimated=False,
                    stale=False
                )
                return

            # 3. Calculate median for outlier detection
            median_price = statistics.median(prices)
            threshold = settings.consensus.outlier_threshold_percent / 100.0

            # Filter valid feeds (within outlier threshold)
            valid_feeds = {}
            outlier_feeds = {}

            for source, feed in active_feeds.items():
                price_dev = abs(feed["price"] - median_price) / median_price
                if price_dev <= threshold:
                    valid_feeds[source] = feed
                else:
                    outlier_feeds[source] = feed
                    logger.warning(f"Outlier detected for {commodity} from source '{source}': "
                                   f"Price {feed['price']} deviated from median {median_price:.2f}")
                    # Report outlier to collector manager to reduce health score
                    if self.collector_manager:
                        self.collector_manager.record_failure(source, failure_type="outlier")

            # If all sources were marked outliers, fallback to median
            if not valid_feeds:
                logger.warning(f"All active feeds marked as outliers for {commodity}. Falling back to median.")
                valid_feeds = active_feeds

            # 4. Compute final consensus values
            valid_prices = [f["price"] for f in valid_feeds.values()]
            consensus_price = statistics.mean(valid_prices)
            
            # Calculate confidence score based on standard deviation
            if len(valid_prices) > 1:
                std_dev = statistics.stdev(valid_prices)
                # StdDev relative to consensus price
                relative_std = (std_dev / consensus_price) * 100.0
                # Higher variance = lower confidence
                confidence = max(0.0, min(100.0, 100.0 - (relative_std * 10.0)))
            else:
                confidence = 60.0  # Safe default for single non-outlier source

            # Select the primary collector based on the healthiest/highest ranked source
            primary_source = list(valid_feeds.keys())[0]
            # Calculate average latency of valid sources
            avg_latency = int(statistics.mean([f["latency_ms"] for f in valid_feeds.values()]))

            await self.commit_consensus(
                commodity=commodity,
                price=consensus_price,
                collector=primary_source,
                latency_ms=avg_latency,
                confidence=round(confidence, 2),
                source_count=len(valid_feeds),
                estimated=False,
                stale=False
            )

    async def commit_consensus(self, commodity: str, price: float, collector: str, latency_ms: int,
                               confidence: float, source_count: int, estimated: bool, stale: bool, reason: str = None):
        """
        Commits validated price ticks to PostgreSQL, updates Redis, and broadcasts live.
        """
        now = datetime.now(timezone.utc)
        
        # Compute daily stats (Open, High, Low, Close) relative to database values
        ohlc = await self._calculate_ohlc(commodity, price, now)

        validated_tick = {
            "commodity": commodity,
            "price": round(price, 2),
            "change": ohlc["change"],
            "change_percent": ohlc["change_percent"],
            "updated_at": now.isoformat(),
            "collector": collector,
            "collector_latency_ms": latency_ms,
            "confidence": confidence,
            "source_count": source_count,
            "estimated": estimated,
            "stale": stale,
            "open": ohlc["open"],
            "high": ohlc["high"],
            "low": ohlc["low"],
            "close": ohlc["close"],
            "volume": 0.0  # MCX Volume is rarely served by free sources
        }

        # Keep a copy in memory for fast fail-safe checks
        last_validated_ticks[commodity] = validated_tick

        # 1. Update Redis LTP Cache
        await redis_client.set_ltp(commodity, validated_tick)

        # 2. Publish to Redis Pub/Sub for WebSockets
        await redis_client.publish_tick(commodity, validated_tick)

        # 3. Write ValidatedTick to PostgreSQL asynchronously
        def db_write():
            db = SessionLocal()
            try:
                val_model = ValidatedTick(
                    commodity=commodity,
                    price=validated_tick["price"],
                    change=validated_tick["change"],
                    change_percent=validated_tick["change_percent"],
                    timestamp=now,
                    collector=collector,
                    collector_latency_ms=latency_ms,
                    confidence=confidence,
                    source_count=source_count,
                    estimated=estimated,
                    stale=stale,
                    open=ohlc["open"],
                    high=ohlc["high"],
                    low=ohlc["low"],
                    close=ohlc["close"]
                )
                db.add(val_model)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to write validated tick to DB: {e}")
            finally:
                db.close()

        await asyncio.to_thread(db_write)
        logger.debug(f"Consensus committed for {commodity}: {validated_tick['price']} (conf: {confidence}%)")

    async def trigger_failsafe(self, commodity: str, reason: str):
        """
        Executed when all primary live sources fail.
        Invokes the emergency proxy fallback if enabled, or falls back to serving stale data.
        """
        logger.warning(f"Consensus failed for {commodity}: {reason}. Activating fail-safe protocol.")
        
        # 1. Check if the emergency proxy is configured and available
        proxy_data = None
        if settings.consensus.fallback_to_proxy_on_failure and self.collector_manager:
            proxy_collector = self.collector_manager.get_collector("yfinance_proxy")
            if proxy_collector and proxy_collector.is_healthy:
                try:
                    # Run emergency proxy collection directly
                    proxy_res = await proxy_collector.collect()
                    if proxy_res and commodity in proxy_res:
                        proxy_data = proxy_res[commodity]
                except Exception as e:
                    logger.error(f"Failed calling emergency proxy: {e}")

        if proxy_data:
            logger.info(f"Serving proxy estimated price for {commodity}.")
            await self.commit_consensus(
                commodity=commodity,
                price=proxy_data["price"],
                collector="yfinance_proxy",
                latency_ms=proxy_data.get("latency_ms", 100),
                confidence=settings.consensus.proxy_confidence,
                source_count=1,
                estimated=True,
                stale=False,
                reason="All live MCX collectors unavailable"
            )
            return

        # 2. Fall back to serving the last verified price as stale
        stale_tick = last_validated_ticks[commodity]
        
        # If we don't have it in memory, fetch it from Redis
        if not stale_tick:
            stale_tick = await redis_client.get_ltp(commodity)

        # If we don't have it in Redis, fetch from DB
        if not stale_tick:
            stale_tick = await self._fetch_last_tick_from_db(commodity)

        if stale_tick:
            logger.warning(f"Serving last known verified price for {commodity} as STALE.")
            # Set stale flags and calculate price age
            now = datetime.now(timezone.utc)
            tick_time = datetime.fromisoformat(stale_tick["updated_at"].replace("Z", "+00:00"))
            data_age_ms = int((now - tick_time).total_seconds() * 1000)
            
            stale_tick["stale"] = True
            stale_tick["estimated"] = False
            stale_tick["data_age_ms"] = data_age_ms
            stale_tick["confidence"] = 0.0
            stale_tick["source_count"] = 0
            
            # Broadcast the stale status
            await redis_client.set_ltp(commodity, stale_tick)
            await redis_client.publish_tick(commodity, stale_tick)
        else:
            logger.critical(f"Fail-safe failed: No historical data available for {commodity}!")

    async def _calculate_ohlc(self, commodity: str, current_price: float, now: datetime) -> Dict[str, Any]:
        """
        Finds the daily Open, High, Low, and Close bounds using high-performance database-backed queries.
        """
        def query_ohlc():
            db = SessionLocal()
            try:
                from sqlalchemy import func
                today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
                
                # Fetch open price of today (first tick of today)
                open_tick = db.query(ValidatedTick.price).filter(
                    ValidatedTick.commodity == commodity,
                    ValidatedTick.timestamp >= today_start
                ).order_by(ValidatedTick.timestamp.asc()).first()
                
                open_val = open_tick[0] if open_tick else current_price
                
                # Fetch today's high and low (excluding current price, we will compare below)
                stats = db.query(
                    func.max(ValidatedTick.price),
                    func.min(ValidatedTick.price)
                ).filter(
                    ValidatedTick.commodity == commodity,
                    ValidatedTick.timestamp >= today_start
                ).first()
                
                db_high, db_low = stats if stats else (None, None)
                high_val = max(db_high, current_price) if db_high is not None else current_price
                low_val = min(db_low, current_price) if db_low is not None else current_price

                # Fetch yesterday's last price for change calculation
                yesterday_last = db.query(ValidatedTick.price).filter(
                    ValidatedTick.commodity == commodity,
                    ValidatedTick.timestamp < today_start
                ).order_by(ValidatedTick.timestamp.desc()).first()

                prev_close = yesterday_last[0] if yesterday_last else open_val
                change = current_price - prev_close
                change_percent = (change / prev_close) * 100.0 if prev_close else 0.0

                return {
                    "open": open_val,
                    "high": high_val,
                    "low": low_val,
                    "close": current_price,
                    "change": round(change, 2),
                    "change_percent": round(change_percent, 4)
                }
            except Exception as e:
                logger.error(f"Error calculating OHLC: {e}")
                return {
                    "open": current_price,
                    "high": current_price,
                    "low": current_price,
                    "close": current_price,
                    "change": 0.0,
                    "change_percent": 0.0
                }
            finally:
                db.close()

        return await asyncio.to_thread(query_ohlc)

    async def _fetch_last_tick_from_db(self, commodity: str) -> Optional[Dict[str, Any]]:
        def query_last():
            db = SessionLocal()
            try:
                t = db.query(ValidatedTick).filter(
                    ValidatedTick.commodity == commodity
                ).order_by(ValidatedTick.timestamp.desc()).first()
                if t:
                    return {
                        "commodity": t.commodity,
                        "price": t.price,
                        "change": t.change,
                        "change_percent": t.change_percent,
                        "updated_at": t.timestamp.isoformat(),
                        "collector": t.collector,
                        "collector_latency_ms": t.collector_latency_ms,
                        "confidence": t.confidence,
                        "source_count": t.source_count,
                        "estimated": t.estimated,
                        "stale": t.stale,
                        "open": t.open,
                        "high": t.high,
                        "low": t.low,
                        "close": t.close,
                        "volume": t.volume
                    }
            except Exception as e:
                logger.error(f"Error querying last tick: {e}")
            finally:
                db.close()
            return None

        return await asyncio.to_thread(query_last)

consensus_engine = ConsensusEngine()
