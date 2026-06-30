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
        Verifies Yahoo Finance API connectivity.
        """
        try:
            res = await self.client.head("https://query1.finance.yahoo.com/", timeout=3.0)
            return res.status_code < 400
        except Exception:
            return False

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

            if res_silver.status_code != 200:
                logger.error(f"Failed to fetch COMEX Silver: HTTP {res_silver.status_code}")
                return None
            silver_data = res_silver.json()
            comex_silver = silver_data["chart"]["result"][0]["meta"]["regularMarketPrice"]

            # 4. Landed price math approximations
            # 1 troy ounce = 31.1034768 grams. Gold traded in India in blocks of 10 grams.
            price_gold_per_gram_usd = comex_gold / 31.1034768
            price_gold_10g_inr_raw = price_gold_per_gram_usd * 10 * usdinr_rate
            # Apply Customs + AIDC (Cess) + GST
            landed_gold = price_gold_10g_inr_raw * (1 + self.customs_duty + self.aidc) * (1 + self.gst)

            # 1 troy ounce = 0.0311034768 kg. Silver traded in India in blocks of 1 kg.
            # 1 kg = 32.1507 troy ounces.
            price_silver_1kg_inr_raw = comex_silver * 32.150746 * usdinr_rate
            # Apply Customs + AIDC + GST
            landed_silver = price_silver_1kg_inr_raw * (1 + self.customs_duty + self.aidc) * (1 + self.gst)

            raw_payload = {
                "comex_gold": comex_gold,
                "comex_silver": comex_silver,
                "usdinr_rate": usdinr_rate,
                "timestamp": time.time()
            }

            print(f"Parsed Gold: Rs. {round(landed_gold, 2)} (COMEX: ${comex_gold}, USDINR: {usdinr_rate})")
            print(f"Parsed Silver: Rs. {round(landed_silver, 2)} (COMEX: ${comex_silver})")
            
            # Extracted source timestamp
            try:
                g_meta = gold_data["chart"]["result"][0]["meta"]
                source_ts = datetime.fromtimestamp(g_meta["regularMarketTime"], tz=timezone.utc).isoformat()
            except Exception:
                source_ts = "N/A"
            print(f"Source Timestamp: {source_ts}")
            print(f"========================================\n")

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
