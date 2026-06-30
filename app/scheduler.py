from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
from datetime import datetime, timezone, timedelta
from app.config import settings
from app.database import SessionLocal
from app.models import APIKey, RawTick
from app.redis_client import redis_client
from app.collector_manager import collector_manager

logger = logging.getLogger("Scheduler")
scheduler = AsyncIOScheduler()

async def sync_api_key_usage_to_db():
    """
    Syncs API key request counts cached in Redis to the PostgreSQL database.
    """
    logger.debug("Syncing API key usage from Redis to DB...")
    db = SessionLocal()
    try:
        # Search all usage keys in Redis using async scan_iter
        async for key in redis_client.client.scan_iter("usage:monthly:*"):
            key_str = str(key)
            api_key_val = key_str.replace("usage:monthly:", "")
            
            # Fetch increment count
            count_val = await redis_client.client.get(key_str)
            count = int(count_val) if count_val else 0
            if count > 0:
                api_key = db.query(APIKey).filter(APIKey.key_value == api_key_val).first()
                if api_key:
                    api_key.monthly_usage += count
                    db.commit()
                    # Decrease or reset in Redis
                    await redis_client.client.decrby(key_str, count)
    except Exception as e:
        logger.error(f"Error in sync_api_key_usage_to_db job: {e}")
    finally:
        db.close()

async def collector_health_scan_job():
    """
    Triggers dynamic ranking recalculation inside the CollectorManager.
    """
    logger.debug("Running collector dynamic ranking health scan...")
    try:
        collector_manager.calculate_ranks()
    except Exception as e:
        logger.error(f"Error in collector_health_scan_job: {e}")

async def cleanup_database_job():
    """
    Cleans up raw ticks older than config.db_cleanup_days.
    """
    logger.info("Running database optimization and tick cleanup job...")
    db = SessionLocal()
    try:
        cleanup_date = datetime.now(timezone.utc) - timedelta(days=settings.platform.db_cleanup_days)
        
        # 1. Delete raw ticks
        deleted_raw = db.query(RawTick).filter(RawTick.timestamp < cleanup_date).delete(synchronize_session=False)
        db.commit()
        logger.info(f"Database cleanup: Deleted {deleted_raw} raw ticks older than {settings.platform.db_cleanup_days} days.")
        
        # 2. Expired API Keys cleanup (Deactivates them instead of deleting, for reference)
        expired_keys = db.query(APIKey).filter(
            APIKey.expires_at != None,
            APIKey.expires_at < datetime.now(timezone.utc),
            APIKey.is_active == True
        ).all()
        for k in expired_keys:
            k.is_active = False
            logger.info(f"Deactivated expired API key for {k.owner}")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error in cleanup_database_job: {e}")
    finally:
        db.close()

def start_scheduler():
    logger.info("Initializing APScheduler background jobs...")
    
    # 1. Sync usage every 1 minute
    scheduler.add_job(sync_api_key_usage_to_db, "interval", minutes=1)
    
    # 2. Recalculate collector ranking scores every 30 seconds
    scheduler.add_job(collector_health_scan_job, "interval", seconds=30)
    
    # 3. Clean up raw ticks once daily
    scheduler.add_job(cleanup_database_job, "cron", hour=1, minute=0)
    
    scheduler.start()
    logger.info("Scheduler started successfully.")

def shutdown_scheduler():
    logger.info("Stopping APScheduler...")
    scheduler.shutdown()
    logger.info("Scheduler stopped.")
