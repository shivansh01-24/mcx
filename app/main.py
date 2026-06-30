import asyncio
import time
import uuid
import logging
import psutil
import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, Query, Path, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, init_db, SessionLocal
from app.models import ValidatedTick, RawTick, APIKey, CollectorMetricModel
from app.redis_client import redis_client
from app.event_bus import event_bus
from app.consensus import consensus_engine, last_validated_ticks
from app.collector_manager import collector_manager
from app.scheduler import start_scheduler, shutdown_scheduler
from app.auth import get_api_key
from app.schemas import SaaSResponse, NormalizedPriceData, HistoryCandle, SystemStatusData, CollectorMetricData, APIKeyResponse

# Set up logging
logging.basicConfig(
    level=getattr(logging, settings.platform.log_level.upper(), logging.INFO),
    format='{"timestamp":"%(asctime)s", "level":"%(levelname)s", "logger":"%(name)s", "message":%(message)s}'
)
# Ensure logs are formatted as valid JSON strings
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage()
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

logger = logging.getLogger("Main")

app = FastAPI(
    title="Dedicated MCX Gold & Silver API Platform",
    version=settings.platform.version,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Enable CORS for cross-origin developer requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static folder for Developer Dashboard
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# REST API Envelope Middleware
@app.middleware("http")
async def envelope_middleware(request, call_next):
    # Skip formatting for non-API routes (docs, metrics, static assets)
    path = request.url.path
    if not path.startswith("/api/v1/"):
        return await call_next(request)

    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
    t_start = time.time()
    
    try:
        response = await call_next(request)
        latency = (time.time() - t_start) * 1000
        
        # If response is already in JSONResponse format, we check if we need to envelope it
        # FastAPI might return streaming or custom responses. We handle standard JSON responses
        if response.headers.get("content-type") == "application/json":
            # Extract body
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk
            
            try:
                import json
                data = json.loads(body_bytes.decode("utf-8"))
            except:
                data = body_bytes.decode("utf-8")
                
            # If the response already has 'success' key, it is already enveloped (e.g. from an exception handler)
            if isinstance(data, dict) and "success" in data:
                envelope = data
            else:
                envelope = {
                    "success": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": round(latency, 2),
                    "request_id": request_id,
                    "data": data,
                    "error": None
                }
            
            headers = {
                "X-Request-ID": request_id,
                "X-Platform-Version": settings.platform.version,
                "X-Cache": "HIT" if "ltp:" in path or "price" in path else "MISS"
            }
            
            # Fetch cache ltp stats to include custom response headers
            if "gold" in path or "prices" in path:
                ltp = await redis_client.get_ltp("gold")
                if ltp:
                    headers["X-Collector"] = ltp.get("collector", "consensus")
                    headers["X-Confidence"] = str(ltp.get("confidence", 100.0))
                    headers["X-Data-Age"] = str(ltp.get("data_age_ms", 0))
            
            return JSONResponse(content=envelope, status_code=response.status_code, headers=headers)
        else:
            return response
    except Exception as e:
        latency = (time.time() - t_start) * 1000
        logger.error(f"Request failed: {e}", exc_info=True)
        envelope = {
            "success": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_ms": round(latency, 2),
            "request_id": request_id,
            "data": None,
            "error": str(e)
        }
        return JSONResponse(
            content=envelope,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            headers={"X-Request-ID": request_id}
        )

# Exception handlers to enforce standard JSON envelopes on errors
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    request_id = uuid.uuid4().hex
    envelope = {
        "success": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latency_ms": 0.0,
        "request_id": request_id,
        "data": None,
        "error": exc.detail
    }
    return JSONResponse(content=envelope, status_code=exc.status_code)

# ----------------- Start & Shutdown Events -----------------

@app.on_event("startup")
async def startup_event():
    logger.info("Initializing Graceful Startup Pipeline...")
    
    # 1. Connect and initialize Database
    try:
        init_db()
    except Exception as e:
        logger.error(f"Database initialization failed on startup: {e}. Proceeding in degraded state.")
    
    # 2. Connect Redis and perform ping
    try:
        if not await redis_client.ping():
            logger.error("Redis connectivity check failed! Proceeding in degraded state.")
        else:
            logger.info("Redis connected successfully.")
    except Exception as e:
        logger.error(f"Redis connection failed on startup: {e}. Proceeding in degraded state.")
    
    # 3. Warm Cache: Load latest validated prices from database to Redis
    try:
        await warm_redis_cache()
    except Exception as e:
        logger.error(f"Cache warming failed on startup: {e}.")
    
    # 4. Start Event Bus workers
    try:
        await event_bus.start_workers()
    except Exception as e:
        logger.error(f"Event Bus workers failed to start: {e}.")
    
    # 5. Register Consensus Engine callback in Event Bus
    consensus_engine.set_collector_manager(collector_manager)
    event_bus.register_consensus_callback(consensus_engine.handle_raw_tick)
    
    # 6. Start Collector Manager (hot plugins loading)
    try:
        await collector_manager.start()
    except Exception as e:
        logger.error(f"Collector Manager failed to start: {e}.")
    
    # 7. Start background Scheduler
    try:
        start_scheduler()
    except Exception as e:
        logger.error(f"Scheduler failed to start: {e}.")
    
    logger.info("Platform initialized. Server accepting requests.")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Initializing Graceful Shutdown Pipeline...")
    
    # 1. Stop background scheduler
    shutdown_scheduler()
    
    # 2. Stop Collector Manager (unloads all collectors)
    await collector_manager.stop()
    
    # 3. Stop Event Bus workers (saves remaining queue items)
    await event_bus.stop_workers()
    
    logger.info("Graceful shutdown completed successfully. Exiting.")

async def warm_redis_cache():
    logger.info("Warming Redis cache with latest prices...")
    db = SessionLocal()
    try:
        for commodity in ["gold", "silver"]:
            t = db.query(ValidatedTick).filter(
                ValidatedTick.commodity == commodity
            ).order_by(ValidatedTick.timestamp.desc()).first()
            if t:
                # Reconstruct dict
                data = {
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
                await redis_client.set_ltp(commodity, data)
                last_validated_ticks[commodity] = data
                logger.info(f"Warmed cache for {commodity}: {t.price}")
    except Exception as e:
        logger.error(f"Error warming Redis cache: {e}")
    finally:
        db.close()

# ----------------- REST Endpoints -----------------

@app.get("/")
async def get_landing_page():
    return FileResponse(os.path.join(STATIC_DIR, "landing.html"))

@app.get("/dashboard")
async def get_dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/api/v1/prices", response_model=List[NormalizedPriceData])
async def get_prices(api_key: APIKey = Depends(get_api_key)):
    """
    Returns latest prices for both Gold and Silver.
    Serves instantly from Redis LTP Cache.
    """
    response_data = []
    for comm in ["gold", "silver"]:
        data = await redis_client.get_ltp(comm)
        if data:
            # Calculate live price age
            now = datetime.now(timezone.utc)
            tick_time = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
            data["data_age_ms"] = int((now - tick_time).total_seconds() * 1000)
            response_data.append(data)
            
    if not response_data:
        raise HTTPException(status_code=503, detail="No market data currently cached.")
    return response_data

@app.get("/api/v1/gold", response_model=NormalizedPriceData)
async def get_gold_price(api_key: APIKey = Depends(get_api_key)):
    """
    Returns latest price for MCX Gold.
    """
    data = await redis_client.get_ltp("gold")
    if not data:
        raise HTTPException(status_code=503, detail="Gold price data not cached.")
    now = datetime.now(timezone.utc)
    tick_time = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    data["data_age_ms"] = int((now - tick_time).total_seconds() * 1000)
    return data

@app.get("/api/v1/silver", response_model=NormalizedPriceData)
async def get_silver_price(api_key: APIKey = Depends(get_api_key)):
    """
    Returns latest price for MCX Silver.
    """
    data = await redis_client.get_ltp("silver")
    if not data:
        raise HTTPException(status_code=503, detail="Silver price data not cached.")
    now = datetime.now(timezone.utc)
    tick_time = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    data["data_age_ms"] = int((now - tick_time).total_seconds() * 1000)
    return data

@app.get("/api/v1/history/{commodity}", response_model=List[HistoryCandle])
async def get_history(
    commodity: str = Path(..., description="Commodity name ('gold' or 'silver')"),
    interval: str = Query("1m", description="Interval: 1s, 5s, 1m, 5m, 15m, 1h, 1d"),
    start_time: Optional[datetime] = Query(None, description="Start ISO datetime"),
    end_time: Optional[datetime] = Query(None, description="End ISO datetime"),
    limit: int = Query(500, description="Max candle records to return", le=2000),
    api_key: APIKey = Depends(get_api_key),
    db: Session = Depends(get_db)
):
    """
    Returns historical candle aggregations for MCX Gold and Silver.
    Retrieves database tick logs and aggregates them portable in memory.
    """
    comm = commodity.lower()
    if comm not in ["gold", "silver"]:
        raise HTTPException(status_code=400, detail="Invalid commodity. Supported: 'gold', 'silver'.")

    # Default query bounds
    if not end_time:
        end_time = datetime.now(timezone.utc)
    if not start_time:
        start_time = end_time - timedelta(hours=6)

    # Fetch validated ticks
    ticks = db.query(ValidatedTick).filter(
        ValidatedTick.commodity == comm,
        ValidatedTick.timestamp >= start_time,
        ValidatedTick.timestamp <= end_time
    ).order_by(ValidatedTick.timestamp.asc()).all()

    if not ticks:
        return []

    # Map intervals to timedelta
    interval_mapping = {
        "1s": timedelta(seconds=1),
        "5s": timedelta(seconds=5),
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1)
    }
    
    if interval not in interval_mapping:
        raise HTTPException(status_code=400, detail=f"Invalid interval. Choose from: {list(interval_mapping.keys())}")
        
    delta = interval_mapping[interval]

    # Perform OHLC candle grouping in memory
    candles: List[HistoryCandle] = []
    
    current_bucket_start = None
    current_bucket_ticks = []

    def get_bucket_time(dt: datetime, interval_str: str) -> datetime:
        """Truncates time to the nearest interval bucket"""
        if interval_str == "1s":
            return dt.replace(microsecond=0)
        elif interval_str == "5s":
            sec = dt.second - (dt.second % 5)
            return dt.replace(second=sec, microsecond=0)
        elif interval_str == "1m":
            return dt.replace(second=0, microsecond=0)
        elif interval_str == "5m":
            minute = dt.minute - (dt.minute % 5)
            return dt.replace(minute=minute, second=0, microsecond=0)
        elif interval_str == "15m":
            minute = dt.minute - (dt.minute % 15)
            return dt.replace(minute=minute, second=0, microsecond=0)
        elif interval_str == "1h":
            return dt.replace(minute=0, second=0, microsecond=0)
        elif interval_str == "1d":
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt

    for tick in ticks:
        tick_time = tick.timestamp.replace(tzinfo=timezone.utc) if tick.timestamp.tzinfo is None else tick.timestamp
        bucket_time = get_bucket_time(tick_time, interval)
        
        if current_bucket_start is None:
            current_bucket_start = bucket_time
            current_bucket_ticks.append(tick)
        elif bucket_time == current_bucket_start:
            current_bucket_ticks.append(tick)
        else:
            # Finalize current candle
            p_list = [t.price for t in current_bucket_ticks]
            candles.append(HistoryCandle(
                timestamp=current_bucket_start.isoformat(),
                open=current_bucket_ticks[0].price,
                high=max(p_list),
                low=min(p_list),
                close=current_bucket_ticks[-1].price,
                volume=sum(t.volume or 0.0 for t in current_bucket_ticks)
            ))
            
            # Start new candle bucket
            current_bucket_start = bucket_time
            current_bucket_ticks = [tick]

    # Add the final trailing candle
    if current_bucket_ticks:
        p_list = [t.price for t in current_bucket_ticks]
        candles.append(HistoryCandle(
            timestamp=current_bucket_start.isoformat(),
            open=current_bucket_ticks[0].price,
            high=max(p_list),
            low=min(p_list),
            close=current_bucket_ticks[-1].price,
            volume=sum(t.volume or 0.0 for t in current_bucket_ticks)
        ))

    # Apply limits
    return candles[-limit:]

@app.get("/api/v1/search")
async def search_symbols(q: str = Query(..., min_length=1), api_key: APIKey = Depends(get_api_key)):
    """
    Fuzzy symbol search helper.
    """
    symbols = [
        {"symbol": "MCX_GOLD", "name": "MCX Gold Futures (1kg)", "exchange": "MCX", "commodity": "gold"},
        {"symbol": "MCX_SILVER", "name": "MCX Silver Futures (30kg)", "exchange": "MCX", "commodity": "silver"}
    ]
    query = q.lower()
    matches = [s for s in symbols if query in s["symbol"].lower() or query in s["name"].lower()]
    return matches

@app.get("/api/v1/symbols")
async def get_symbols(api_key: APIKey = Depends(get_api_key)):
    return [
        {"symbol": "MCX_GOLD", "exchange": "MCX", "commodity": "gold"},
        {"symbol": "MCX_SILVER", "exchange": "MCX", "commodity": "silver"}
    ]

@app.get("/api/v1/exchanges")
async def get_exchanges(api_key: APIKey = Depends(get_api_key)):
    return [{"name": "MCX", "description": "Multi Commodity Exchange of India"}]

@app.get("/api/v1/status")
async def get_system_status(api_key: APIKey = Depends(get_api_key), db: Session = Depends(get_db)):
    """
    Diagnostics monitor listing database, redis, active source stats, and collector metrics.
    """
    # 1. DB check
    try:
        db.execute("SELECT 1")
        db_status = "CONNECTED"
    except:
        db_status = "DISCONNECTED"

    # 2. Redis check
    redis_status = "CONNECTED" if redis_client.ping() else "DISCONNECTED"

    # 3. Active sources
    active_source = {
        "gold": collector_manager.get_best_collector_for("gold") or "None",
        "silver": collector_manager.get_best_collector_for("silver") or "None"
    }

    # 4. Collector details
    collectors_list = []
    for name, metrics in collector_manager.metrics.items():
        col = collector_manager.get_collector(name)
        manifest = col.MANIFEST if col else {}
        
        # Calculate health score: success_rate - (consecutive failures * 20)
        h_score = metrics["success_rate"] - (metrics["consecutive_failures"] * 20.0)
        if metrics["circuit_breaker_status"] == "OPEN":
            h_score = 0.0
        h_score = max(0.0, h_score)

        collectors_list.append(CollectorMetricData(
            collector_name=name,
            version=manifest.get("version", "1.0.0"),
            collector_type=manifest.get("collector_type", "HTML"),
            exchange=manifest.get("exchange", "MCX"),
            avg_latency_ms=round(metrics["avg_latency"], 1),
            success_rate=round(metrics["success_rate"], 1),
            consecutive_failures=metrics["consecutive_failures"],
            circuit_breaker_status=metrics["circuit_breaker_status"],
            last_successful_update=metrics["last_successful_update"].isoformat() if metrics["last_successful_update"] else None,
            health_score=round(h_score, 1)
        ))

    # 5. OS Stats
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().used / (1024 * 1024)
    disk = psutil.disk_usage("/").percent

    status_data = SystemStatusData(
        database=db_status,
        redis=redis_status,
        active_source=active_source,
        collectors=collectors_list,
        websocket_connections=len(ws_manager.active_connections),
        cpu_usage_percent=cpu,
        memory_usage_mb=mem,
        disk_usage_percent=disk
    )
    return status_data

@app.get("/api/v1/health")
async def health_check():
    return {"status": "healthy", "time": datetime.now(timezone.utc).isoformat()}

@app.get("/api/v1/version")
async def get_version():
    return {"version": settings.platform.version, "api_version": "v1"}

@app.get("/api/v1/server-time")
async def get_server_time():
    return {"server_time": datetime.now(timezone.utc).isoformat()}

@app.get("/api/v1/sources")
async def get_sources(api_key: APIKey = Depends(get_api_key)):
    sources = []
    for name, instance in collector_manager.collectors.items():
        sources.append(instance.MANIFEST)
    return sources

@app.get("/api/v1/changelog")
async def get_changelog():
    return {
        "changelog": [
            {"version": "1.0.0", "date": "2026-06-30", "description": "Initial release of Dedicated MCX Gold & Silver API Platform."}
        ]
    }

@app.get("/api/v1/docs/postman")
async def get_postman_collection():
    """
    Returns a dynamically constructed Postman Collection JSON.
    """
    postman = {
        "info": {
            "name": "MCX Gold & Silver API Platform",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "item": [
            {
                "name": "Get Gold & Silver Prices",
                "request": {
                    "method": "GET",
                    "header": [{"key": "X-API-Key", "value": "mcx_pub_dev_key"}],
                    "url": {"raw": "{{base_url}}/api/v1/prices", "host": ["{{base_url}}"], "path": ["api", "v1", "prices"]}
                }
            },
            {
                "name": "Get Gold Price",
                "request": {
                    "method": "GET",
                    "header": [{"key": "X-API-Key", "value": "mcx_pub_dev_key"}],
                    "url": {"raw": "{{base_url}}/api/v1/gold", "host": ["{{base_url}}"], "path": ["api", "v1", "gold"]}
                }
            },
            {
                "name": "Get Gold History (5m intervals)",
                "request": {
                    "method": "GET",
                    "header": [{"key": "X-API-Key", "value": "mcx_pub_dev_key"}],
                    "url": {
                        "raw": "{{base_url}}/api/v1/history/gold?interval=5m",
                        "host": ["{{base_url}}"],
                        "path": ["api", "v1", "history", "gold"],
                        "query": [{"key": "interval", "value": "5m"}]
                    }
                }
            }
        ]
    }
    return postman

# ----------------- Admin REST Endpoints -----------------

@app.post("/api/v1/admin/keys", response_model=APIKeyResponse)
async def generate_api_key(
    owner: str = Query(...),
    plan: str = Query("free"),
    description: Optional[str] = Query(None),
    ip_whitelist: Optional[str] = Query(None),
    api_key: APIKey = Depends(get_api_key),
    db: Session = Depends(get_db)
):
    """
    Admin command to generate new developer keys.
    Requires an active API key to authorize (seeding guarantees at least one admin/unlimited key exists).
    """
    if api_key.plan.lower() != "unlimited":
        raise HTTPException(status_code=403, detail="Admin permissions required to generate API Keys.")

    plan_limits = settings.plans.get(plan.lower())
    if not plan_limits:
        raise HTTPException(status_code=400, detail=f"Invalid plan name. Supported plans: {list(settings.plans.keys())}")

    new_key_str = f"mcx_key_{uuid.uuid4().hex[:16]}"
    
    # Expiry set to 1 year
    expiry = datetime.now(timezone.utc) + timedelta(days=365)

    new_key = APIKey(
        key_value=new_key_str,
        plan=plan.lower(),
        rate_limit_per_minute=plan_limits.rate_limit_per_minute,
        daily_quota=plan_limits.daily_quota,
        owner=owner,
        description=description,
        ip_whitelist=ip_whitelist,
        expires_at=expiry,
        is_active=True
    )
    
    try:
        db.add(new_key)
        db.commit()
        db.refresh(new_key)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating API Key: {e}")

    return APIKeyResponse(
        key_value=new_key.key_value,
        plan=new_key.plan,
        owner=new_key.owner,
        description=new_key.description or "",
        is_active=new_key.is_active,
        expires_at=new_key.expires_at.isoformat(),
        rate_limit_per_minute=new_key.rate_limit_per_minute,
        daily_quota=new_key.daily_quota,
        monthly_usage=0
    )

@app.delete("/api/v1/admin/keys/{key_value}")
async def revoke_api_key(
    key_value: str,
    api_key: APIKey = Depends(get_api_key),
    db: Session = Depends(get_db)
):
    if api_key.plan.lower() != "unlimited":
        raise HTTPException(status_code=403, detail="Admin permissions required to revoke API Keys.")
        
    target_key = db.query(APIKey).filter(APIKey.key_value == key_value).first()
    if not target_key:
        raise HTTPException(status_code=404, detail="API Key not found.")
        
    try:
        target_key.is_active = False
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error revoking API Key: {e}")
        
    # Delete from rate limiting Redis caches
    await redis_client.client.delete(f"rate:minute:{key_value}")
    
    return {"success": True, "message": f"API Key '{key_value}' successfully revoked."}

@app.post("/api/v1/admin/collectors/{name}/toggle")
async def toggle_collector(
    name: str,
    active: bool = Query(...),
    api_key: APIKey = Depends(get_api_key)
):
    if api_key.plan.lower() != "unlimited":
        raise HTTPException(status_code=403, detail="Admin permissions required to manage collectors.")
        
    col = collector_manager.get_collector(name)
    if not col:
        raise HTTPException(status_code=404, detail=f"Collector '{name}' not found.")
        
    col.is_active = active
    logger.info(f"Collector '{name}' active state set to {active} by admin.")
    return {"success": True, "message": f"Collector '{name}' state changed to active={active}."}

# ----------------- Historical Replay Route -----------------

@app.get("/api/v1/history/replay")
async def get_history_replay(
    commodity: str = Query(...),
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    api_key: APIKey = Depends(get_api_key),
    db: Session = Depends(get_db)
):
    """
    API endpoint to replay raw tick updates from database logs.
    Useful for system diagnostic, validation tests, and algorithms replay.
    """
    comm = commodity.lower()
    if comm not in ["gold", "silver"]:
        raise HTTPException(status_code=400, detail="Invalid commodity. Supported: 'gold', 'silver'.")

    ticks = db.query(RawTick).filter(
        RawTick.commodity == comm,
        RawTick.timestamp >= start_time,
        RawTick.timestamp <= end_time
    ).order_by(RawTick.timestamp.asc()).all()

    response_data = []
    for t in ticks:
        response_data.append({
            "commodity": t.commodity,
            "price": t.price,
            "source": t.source,
            "latency_ms": t.latency_ms,
            "timestamp": t.timestamp.isoformat()
        })
    return response_data

# ----------------- WebSocket Server Implementation -----------------

class WebSocketConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket Client connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket Client disconnected. Active connections: {len(self.active_connections)}")

ws_manager = WebSocketConnectionManager()

@app.websocket("/live")
async def websocket_live_endpoint(websocket: WebSocket, api_key: str = Query(...), db: Session = Depends(get_db)):
    """
    Websocket subscription handler. Streams real-time prices via Redis Pub/Sub.
    Supports subscriptions for: 'gold', 'silver', 'all', 'heartbeat', 'status'.
    """
    # 1. Authorize API key
    key_entry = db.query(APIKey).filter(APIKey.key_value == api_key).first()
    if not key_entry or not key_entry.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API Key.")
        return

    await ws_manager.connect(websocket)
    
    # Default subscription channel
    subscribed_channels = {"all"}
    
    # Task list to manage active workers for this connection
    tasks = []

    # Redis listener task for Pub/Sub events
    async def redis_listener():
        pubsub = redis_client.get_pubsub()
        # Subscribe to channels matching client interests
        channels = []
        if "gold" in subscribed_channels or "all" in subscribed_channels:
            channels.append("market:updates:gold")
        if "silver" in subscribed_channels or "all" in subscribed_channels:
            channels.append("market:updates:silver")
            
        if channels:
            await pubsub.subscribe(*channels)
            
        try:
            async for message in pubsub.listen():
                if message and message.get("type") == "message":
                    payload = message["data"]
                    # Send payload directly to client
                    await websocket.send_text(payload)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in ws redis_listener: {e}")
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    # Heartbeat task: sends periodic keep-alives and metrics updates
    async def heartbeat_sender():
        interval = settings.websocket.heartbeat_interval_seconds
        while True:
            try:
                await asyncio.sleep(interval)
                # Send heartbeat message
                heartbeat_payload = {
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "clients_connected": len(ws_manager.active_connections)
                }
                await websocket.send_json(heartbeat_payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error sending heartbeat to ws: {e}")
                break

    # Client messages receiver task (handles dynamic channel subscription requests)
    async def client_receiver():
        nonlocal subscribed_channels
        try:
            while True:
                data = await websocket.receive_json()
                action = data.get("action")  # 'subscribe' or 'unsubscribe'
                channel = data.get("channel") # 'gold', 'silver', 'all', 'status'
                
                if action and channel:
                    if action == "subscribe":
                        subscribed_channels.add(channel.lower())
                    elif action == "unsubscribe" and channel.lower() in subscribed_channels:
                        subscribed_channels.remove(channel.lower())
                        
                    # Restart redis listener with new subscription mapping
                    nonlocal redis_task
                    redis_task.cancel()
                    await asyncio.sleep(0.1)
                    redis_task = asyncio.create_task(redis_listener())
                    
                    await websocket.send_json({
                        "success": True, 
                        "message": f"Successfully updated subscriptions: {list(subscribed_channels)}"
                    })
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"Error in client_receiver: {e}")

    # Spin up workers
    redis_task = asyncio.create_task(redis_listener())
    heartbeat_task = asyncio.create_task(heartbeat_sender())
    receiver_task = asyncio.create_task(client_receiver())
    
    try:
        # Keep socket open and wait on receiver loop
        await receiver_task
    finally:
        # Cleanup tasks on disconnect
        redis_task.cancel()
        heartbeat_task.cancel()
        receiver_task.cancel()
        ws_manager.disconnect(websocket)
