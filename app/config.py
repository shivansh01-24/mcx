import os
import yaml
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

class PlatformConfig(BaseModel):
    version: str = "1.0.0"
    log_level: str = "INFO"
    db_cleanup_days: int = 7
    expired_key_cleanup_minutes: int = 60

class DatabaseConfig(BaseModel):
    url: str = "postgresql://postgres:postgres@localhost:5432/mcx_platform"
    pool_size: int = 20
    max_overflow: int = 10

class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    ttl_seconds: int = 30
    url: Optional[str] = None

class WebSocketConfig(BaseModel):
    heartbeat_interval_seconds: int = 15

class ConsensusConfig(BaseModel):
    outlier_threshold_percent: float = 1.5
    min_required_sources: int = 2
    fallback_to_proxy_on_failure: bool = True
    proxy_confidence: float = 20.0

class RankingWeights(BaseModel):
    priority: float = 50.0
    success_rate: float = 30.0
    latency: float = 15.0
    failures: float = 25.0

class RankingConfig(BaseModel):
    weights: RankingWeights = Field(default_factory=RankingWeights)
    decay_factor: float = 0.9

class RetryConfig(BaseModel):
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 10.0
    timeout_seconds: float = 5.0
    jitter: bool = True

class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = 3
    recovery_timeout_seconds: int = 30

class PlanLimits(BaseModel):
    rate_limit_per_minute: int
    daily_quota: int

class Settings(BaseSettings):
    platform: PlatformConfig = Field(default_factory=PlatformConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)
    consensus: ConsensusConfig = Field(default_factory=ConsensusConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    plans: Dict[str, PlanLimits] = {}

    class Config:
        env_nested_delimiter = "__"
        env_prefix = "MCX_"

def load_settings() -> Settings:
    # Try finding config.yaml in workspace root or current dir
    config_paths = ["config.yaml", "../config.yaml", "g:/mcx/config.yaml"]
    config_data = {}
    for path in config_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    config_data = yaml.safe_load(f) or {}
                break
            except Exception as e:
                print(f"Error loading config at {path}: {e}")
                
    # Environment variable overrides
    # Override via environment variables e.g. MCX_DATABASE__URL
    settings = Settings(**config_data)
    
    # Check if DB URL is defined in env specifically
    env_db_url = os.environ.get("DATABASE_URL")
    if env_db_url:
        settings.database.url = env_db_url
        
    env_redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDISPRIVATE_URL")
    if env_redis_url:
        settings.redis.url = env_redis_url
        
    env_redis_host = os.environ.get("REDIS_HOST")
    if env_redis_host and not env_redis_url:
        settings.redis.host = env_redis_host
        
    return settings

settings = load_settings()
