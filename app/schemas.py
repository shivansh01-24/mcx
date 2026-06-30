from typing import Any, Optional, Dict, List
from pydantic import BaseModel, Field
from datetime import datetime

class NormalizedPriceData(BaseModel):
    commodity: str = Field(..., description="Name of the commodity ('gold' or 'silver')")
    price: float = Field(..., description="Consensus price of the commodity")
    change: Optional[float] = Field(None, description="Daily absolute price change")
    change_percent: Optional[float] = Field(None, description="Daily percentage price change")
    updated_at: str = Field(..., description="ISO 8601 UTC timestamp of validation")
    collector: str = Field(..., description="Collector source responsible for the price")
    collector_latency_ms: int = Field(..., description="Latency of the collector in milliseconds")
    confidence: float = Field(..., description="Confidence score from consensus engine (0-100)")
    data_age_ms: int = Field(..., description="Age of the underlying source data in milliseconds")
    source_count: int = Field(..., description="Number of active sources in consensus calculation")
    estimated: bool = Field(False, description="Flag indicating if price is calculated via fallback proxy")
    stale: bool = Field(False, description="Flag indicating if all feeds are down and serving last known price")
    volume: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None

class SaaSResponse(BaseModel):
    success: bool = Field(..., description="Indicates request success")
    timestamp: str = Field(..., description="ISO 8601 UTC server timestamp")
    latency_ms: float = Field(..., description="API execution latency in milliseconds")
    request_id: str = Field(..., description="Unique correlation identifier for debugging")
    data: Optional[Any] = Field(None, description="Payload data")
    error: Optional[str] = Field(None, description="Error message description if success is False")

class CollectorMetricData(BaseModel):
    collector_name: str
    version: str
    collector_type: str
    exchange: str
    avg_latency_ms: float
    success_rate: float
    consecutive_failures: int
    circuit_breaker_status: str
    last_successful_update: Optional[str]
    health_score: float

class SystemStatusData(BaseModel):
    database: str
    redis: str
    active_source: Dict[str, str]
    collectors: List[CollectorMetricData]
    websocket_connections: int
    cpu_usage_percent: float
    memory_usage_mb: float
    disk_usage_percent: float

class HistoryCandle(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = 0.0

class APIKeyResponse(BaseModel):
    key_value: str
    plan: str
    owner: str
    description: str
    is_active: bool
    expires_at: Optional[str]
    rate_limit_per_minute: int
    daily_quota: int
    monthly_usage: int
