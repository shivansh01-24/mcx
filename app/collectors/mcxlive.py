import httpx
from bs4 import BeautifulSoup
import logging
from typing import Dict, Any, Optional
from app.collectors.base import BaseCollector
from app.config import settings

logger = logging.getLogger("MCXLiveCollector")

class MCXLiveCollector(BaseCollector):
    MANIFEST = {
        "name": "mcxlive",
        "version": "1.0.0",
        "priority": 3,
        "exchange": "MCX",
        "supported_commodities": ["gold", "silver"],
        "polling_interval": 10,
        "collector_type": "HTML",
        "required_env": [],
        "author": "Antigravity Dev Team",
        "min_platform_version": "1.0.0"
    }

    def __init__(self):
        super().__init__()
        self.url = "https://mcxlive.org"
        self.client: Optional[httpx.AsyncClient] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

    async def start(self):
        # Initialize Async httpx client with connection limits
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
        Pings mcxlive.org to verify connection state.
        """
        try:
            res = await self.client.head(self.url, timeout=3.0)
            return res.status_code == 200
        except Exception:
            return False

    async def collect(self) -> Optional[Dict[str, Dict[str, Any]]]:
        import time
        try:
            t_start = time.time()
            res = await self.client.get(self.url)
            latency = int((time.time() - t_start) * 1000)
            
            print(f"\n========================================")
            print(f"Collector: MCXLive")
            print(f"URL: {self.url}")
            print(f"HTTP Method: GET")
            print(f"Status Code: {res.status_code}")
            print(f"Latency: {latency} ms")
            print(f"Response Headers:")
            for k in ["Date", "Cache-Control", "ETag", "Last-Modified", "Content-Type", "Server"]:
                if k in res.headers:
                    print(f"  {k}: {res.headers[k]}")
            print(f"First 500 characters of raw body:")
            print(f"--- START ---")
            print(res.text[:500])
            print(f"--- END ---")
            
            if res.status_code != 200:
                logger.error(f"Failed to fetch mcxlive.org: HTTP {res.status_code}")
                return None

            html = res.text
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", class_="main-table")
            if not table:
                logger.error("Main commodities table not found on mcxlive.org")
                return None

            rows = table.find_all("tr")
            data = {}
            for r in rows:
                cells = r.find_all("td")
                if not cells:
                    continue
                name = cells[0].get_text().strip().lower()
                
                # Check for target commodities
                commodity_key = None
                if "mcx gold" == name:
                    commodity_key = "gold"
                elif "mcx silver" == name:
                    commodity_key = "silver"
                    
                if commodity_key:
                    price_str = cells[1].get_text().replace(",", "").strip()
                    change_str = cells[2].get_text().replace(",", "").replace("+", "").strip()
                    chg_percent_str = cells[3].get_text().replace("%", "").replace(",", "").replace("+", "").strip()
                    
                    price = float(price_str)
                    change = float(change_str)
                    change_percent = float(chg_percent_str)

                    # Extract row payload for logs archiving
                    raw_row_payload = str(r)
                    
                    data[commodity_key] = {
                        "price": price,
                        "change": change,
                        "change_percent": change_percent,
                        "raw_payload": raw_row_payload
                    }
                    
            if not data:
                logger.error("Failed to parse gold or silver row from mcxlive.org")
                return None
            
            print(f"Parsed Gold: Rs. {data.get('gold', {}).get('price')}")
            print(f"Parsed Silver: Rs. {data.get('silver', {}).get('price')}")
            print(f"Source Timestamp: N/A (Live Scraping)")
            print(f"========================================\n")
            return data
        except Exception as e:
            logger.error(f"Error executing collect in MCXLiveCollector: {e}")
            return None
