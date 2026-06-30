import httpx
from bs4 import BeautifulSoup
import json
import logging
from typing import Dict, Any, Optional
from app.collectors.base import BaseCollector
from app.config import settings

logger = logging.getLogger("MoneycontrolCollector")

class MoneycontrolCollector(BaseCollector):
    MANIFEST = {
        "name": "moneycontrol",
        "version": "1.0.0",
        "priority": 2,
        "exchange": "MCX",
        "supported_commodities": ["gold", "silver"],
        "polling_interval": 15,
        "collector_type": "JSON",
        "required_env": [],
        "author": "Antigravity Dev Team",
        "min_platform_version": "1.0.0",
        "experimental": True
    }

    def __init__(self):
        super().__init__()
        self.url = "https://www.moneycontrol.com/commodity/"
        self.client: Optional[httpx.AsyncClient] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Referer": "https://www.google.com/"
        }

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
            print(f"Collector: MoneyControl")
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
                logger.error(f"Failed to fetch Moneycontrol: HTTP {res.status_code}")
                return None

            html = res.text
            soup = BeautifulSoup(html, "html.parser")
            
            # Try to load from __NEXT_DATA__
            next_data = soup.find("script", id="__NEXT_DATA__")
            if next_data:
                try:
                    payload = json.loads(next_data.string)
                    # Traverse next data props pageProps to find commodity info
                    page_props = payload.get("props", {}).get("pageProps", {})
                    # Moneycontrol page layout contains dynamic grids in props. We search for list items
                    comm_list = page_props.get("data", {}).get("commodityList", []) or page_props.get("commodityList", [])
                    
                    data = {}
                    for item in comm_list:
                        # item keys: name, ltp, chg, chg_percent
                        name = item.get("name", "").lower()
                        commodity_key = None
                        if "gold" in name and "mini" not in name and "petal" not in name:
                            commodity_key = "gold"
                        elif "silver" in name and "mini" not in name and "micro" not in name:
                            commodity_key = "silver"

                        if commodity_key:
                            price = float(str(item.get("ltp", 0)).replace(",", ""))
                            change = float(str(item.get("chg", 0)).replace(",", ""))
                            change_percent = float(str(item.get("chg_percent", 0)).replace("%", ""))
                            
                            data[commodity_key] = {
                                "price": price,
                                "change": change,
                                "change_percent": change_percent,
                                "raw_payload": str(item)
                            }
                    if data:
                        return data
                except Exception as ex:
                    logger.debug(f"Parsing __NEXT_DATA__ failed, falling back to HTML parsing: {ex}")

            # Fallback: Parse the HTML table directly
            # Moneycontrol lists commodities inside tables with class 'index-table' or 'mcx_table'
            tables = soup.find_all("table")
            data = {}
            for table in tables:
                rows = table.find_all("tr")
                for r in rows:
                    cells = r.find_all("td")
                    if len(cells) < 4:
                        continue
                    name = cells[0].get_text().strip().lower()
                    
                    commodity_key = None
                    if "gold" in name and "mini" not in name and "petal" not in name:
                        commodity_key = "gold"
                    elif "silver" in name and "mini" not in name and "micro" not in name:
                        commodity_key = "silver"

                    if commodity_key and commodity_key not in data:
                        try:
                            # 1: ltp, 2: chg, 3: chg%
                            price = float(cells[1].get_text().replace(",", "").strip())
                            change = float(cells[2].get_text().replace(",", "").strip())
                            change_percent = float(cells[3].get_text().replace("%", "").replace(",", "").strip())
                            
                            data[commodity_key] = {
                                "price": price,
                                "change": change,
                                "change_percent": change_percent,
                                "raw_payload": str(r)
                            }
                        except Exception as parse_ex:
                            logger.error(f"Error parsing cells in Moneycontrol fallback: {parse_ex}")
                            
            if data:
                print(f"Parsed Gold: Rs. {data.get('gold', {}).get('price')}")
                print(f"Parsed Silver: Rs. {data.get('silver', {}).get('price')}")
                print(f"Source Timestamp: N/A (Live Scraping)")
                print(f"========================================\n")
                return data
            
            print(f"Parsed Gold: None")
            print(f"Parsed Silver: None")
            print(f"Source Timestamp: None")
            print(f"========================================\n")
            logger.error("No Gold/Silver commodity parsed from Moneycontrol.")
            return None
        except Exception as e:
            logger.error(f"Error executing collect in MoneycontrolCollector: {e}")
            return None
