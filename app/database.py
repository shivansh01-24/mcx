from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
from app.models import Base, APIKey
import time
import logging

logger = logging.getLogger("Database")

# Create engine
engine = create_engine(
    settings.database.url,
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
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
    logger.info("Initializing database...")
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
