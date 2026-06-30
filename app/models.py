from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, Numeric, create_engine
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone

Base = declarative_base()

class RawTick(Base):
    __tablename__ = "raw_ticks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    commodity = Column(String(10), nullable=False, index=True)  # 'gold' or 'silver'
    price = Column(Float, nullable=False)
    source = Column(String(50), nullable=False, index=True)
    latency_ms = Column(Integer, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    raw_payload = Column(Text, nullable=True)

class ValidatedTick(Base):
    __tablename__ = "validated_ticks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    commodity = Column(String(10), nullable=False, index=True)  # 'gold' or 'silver'
    price = Column(Float, nullable=False)
    change = Column(Float, nullable=True)
    change_percent = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    collector = Column(String(50), nullable=False)
    collector_latency_ms = Column(Integer, nullable=False)
    confidence = Column(Float, nullable=False)
    source_count = Column(Integer, nullable=False)
    estimated = Column(Boolean, nullable=False, default=False)
    stale = Column(Boolean, nullable=False, default=False)
    
    # Historical candle metrics (optional/cache-able, but good for aggregate replay)
    volume = Column(Float, nullable=True, default=0.0)
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)

class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_value = Column(String(100), unique=True, nullable=False, index=True)
    plan = Column(String(20), nullable=False, default="free")  # free, developer, premium, unlimited
    rate_limit_per_minute = Column(Integer, nullable=False, default=60)
    daily_quota = Column(Integer, nullable=False, default=5000)
    monthly_usage = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    owner = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    ip_whitelist = Column(Text, nullable=True)  # Comma-separated list of whitelisted IPs
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

class CollectorMetricModel(Base):
    __tablename__ = "collector_metrics"

    collector_name = Column(String(50), primary_key=True)
    avg_latency = Column(Float, nullable=False, default=0.0)
    success_rate = Column(Float, nullable=False, default=100.0)
    failure_rate = Column(Float, nullable=False, default=0.0)
    timeout_rate = Column(Float, nullable=False, default=0.0)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    circuit_breaker_status = Column(String(20), nullable=False, default="CLOSED")
    last_successful_update = Column(DateTime(timezone=True), nullable=True)
    total_calls = Column(Integer, nullable=False, default=0)
    total_failures = Column(Integer, nullable=False, default=0)
    total_timeouts = Column(Integer, nullable=False, default=0)
    total_parsing_failures = Column(Integer, nullable=False, default=0)
    uptime = Column(Float, nullable=False, default=100.0)  # Percentage uptime
