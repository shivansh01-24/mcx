import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.models import Base, APIKey
import time
import logging

logger = logging.getLogger("Database")

# -----------------------------------------------------------------------
# Priority resolution:
#   1. DATABASE_URL env var  (Railway injects this automatically)
#   2. config.yaml / MCX_DATABASE__URL env var  (local / Docker Compose)
# -----------------------------------------------------------------------
def _resolve_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if url:
        # Railway Postgres URLs start with "postgres://" — SQLAlchemy
        # psycopg2 requires "postgresql://"
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    # Fall back to settings (reads config.yaml + MCX_* env vars)
    from app.config import settings
    return settings.database.url


_DB_URL = _resolve_db_url()

# Detect and log connection type at import time
def _describe_db(url: str) -> str:
    if "db:5432" in url or "@db/" in url:
        return "Local Docker Compose (hostname: db)"
    if "localhost" in url or "127.0.0.1" in url:
        return "Local Bare-Metal"
    return "Railway DATABASE_URL"

_DB_TYPE = _describe_db(_DB_URL)
logger.info(f"Database connection type: {_DB_TYPE}")
logger.info(f"Database host: {_DB_URL.split('@')[-1].split('/')[0] if '@' in _DB_URL else _DB_URL[:40]}")

# Create engine using resolved URL
engine = create_engine(
    _DB_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries=5, delay=5):
    """
    Initializes database tables and seeds default dev API keys.
    Retries connectivity if database container is not ready yet.
    """
    logger.info(f"Initializing database ({_DB_TYPE})...")
    for i in range(retries):
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("Database tables verified/created successfully.")

            # Seed default API key if not exists
            db = SessionLocal()
            try:
                dev_key = db.query(APIKey).filter(APIKey.key_value == "mcx_pub_dev_key").first()
                if not dev_key:
                    logger.info("Seeding default API Key: 'mcx_pub_dev_key'")
                    new_key = APIKey(
                        key_value="mcx_pub_dev_key",
                        plan="unlimited",
                        rate_limit_per_minute=10000,
                        daily_quota=9999999,
                        owner="Developer Public Account",
                        description="Default seed key for testing and local API requests.",
                        is_active=True
                    )
                    db.add(new_key)
                    db.commit()
                    logger.info("Default API key seeded successfully.")
            except Exception as e:
                db.rollback()
                logger.error(f"Error seeding database: {e}")
            finally:
                db.close()
            return
        except Exception as e:
            logger.warning(f"Database connection attempt {i+1}/{retries} failed: {e}")
            if i < retries - 1:
                time.sleep(delay)
            else:
                logger.error("Could not connect to database after maximum retries.")
                raise e
