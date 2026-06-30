import httpx
import logging
from typing import Dict, Any, Optional
import time
from datetime import datetime, timezone
from app.collectors.base import BaseCollector
from app.config import settings

logger = logging.getLogger("YFinanceProxyCollector")

class YFinanceProxyCollector(BaseCollector):
    MANIFEST = {
        "name": "yfinance_proxy",
        "version": "1.0.0",
        "priority": 4,  # Lower base priority, acts as emergency fallback
        "exchange": "PROXY",
        "supported_commodities": ["gold", "silver"],
        "polling_interval": 60,
        "collector_type": "JSON",
        "required_env": [],
        "author": "Antigravity Dev Team",
        "min_platform_version": "1.0.0"
    }

    def __init__(self):
        super().__init__()
        self.gold_url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1m&range=1d"
        self.silver_url = "https://query1.finance.yahoo.com/v8/finance/chart/SI=F?interval=1m&range=1d"
        self.usdinr_url = "https://query1.finance.yahoo.com/v8/finance/chart/USDINR=X?interval=1m&range=1d"
        
        self.client: Optional[httpx.AsyncClient] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # Indian custom taxes and cess rates (Post-2024 budget changes)
        self.customs_duty = 0.06  # 6% basic customs duty
        self.aidc = 0.05          # 5% Agriculture Infrastructure Development Cess
        self.gst = 0.03           # 3% Goods and Services Tax

    async def start(self):
        self.client = httpx.AsyncClient(
            headers=self.headers,
            verify=False,
            timeout=settings.retry.timeout_seconds
        )

    async def stop(self):
        if self.client:
            await self.client.aclose()

    async def health_check(self) -> bool:
        """
        yfinance_proxy is an emergency fallback — it does not pre-check
        Yahoo Finance connectivity. Real failures are caught in collect()
        and handled by the circuit breaker.
        """
        return True

    async def collect(self) -> Optional[Dict[str, Dict[str, Any]]]:
        try:
            # 1. Fetch USD/INR exchange rate
            t_start = time.time()
            res_usdinr = await self.client.get(self.usdinr_url)
            if res_usdinr.status_code != 200:
                logger.error(f"Failed to fetch USDINR: HTTP {res_usdinr.status_code}")
                return None
            usdinr_data = res_usdinr.json()
            usdinr_rate = usdinr_data["chart"]["result"][0]["meta"]["regularMarketPrice"]

            # 2. Fetch COMEX Gold price (per Troy Ounce)
            res_gold = await self.client.get(self.gold_url)
            if res_gold.status_code != 200:
                logger.error(f"Failed to fetch COMEX Gold: HTTP {res_gold.status_code}")
                return None
            gold_data = res_gold.json()
            comex_gold = gold_data["chart"]["result"][0]["meta"]["regularMarketPrice"]

            # 3. Fetch COMEX Silver price (per Troy Ounce)
            res_silver = await self.client.get(self.silver_url)
            latency = int((time.time() - t_start) * 1000)
            
            print(f"\n========================================")
            print(f"Collector: YFinanceProxy (Fallback)")
            print(f"URLs Requested:")
            print(f"  USDINR: {self.usdinr_url}")
            print(f"  COMEX Gold: {self.gold_url}")
            print(f"  COMEX Silver: {self.silver_url}")
            print(f"HTTP Method: GET")
            print(f"Status Code: {res_silver.status_code}")
            print(f"Total Latency: {latency} ms")
            print(f"Response Headers (COMEX Gold):")
            for k in ["Date", "Cache-Control", "ETag", "Last-Modified", "Content-Type", "Server"]:
                if k in res_gold.headers:
                    print(f"  {k}: {res_gold.headers[k]}")
            print(f"First 500 characters of COMEX Gold JSON payload:")
            print(f"--- START ---")
            import json as json_print
            print(json_print.dumps(gold_data)[:500])
            print(f"--- END ---")

            return {
                "gold": {
                    "price": round(landed_gold, 2),
                    "raw_payload": str(raw_payload)
                },
                "silver": {
                    "price": round(landed_silver, 2),
                    "raw_payload": str(raw_payload)
                }
            }
        except Exception as e:
            logger.error(f"Error executing collect in YFinanceProxyCollector: {e}")
            return None
