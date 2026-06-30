from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class BaseCollector(ABC):
    # Class-level manifest metadata dictionary, must be defined in child classes
    MANIFEST: Dict[str, Any] = {}

    def __init__(self):
        self.is_active = True
        self.is_healthy = True

    async def start(self):
        """
        Initializes collector dependencies (e.g. connections, sessions, files).
        """
        pass

    async def stop(self):
        """
        Cleans up resources gracefully on unload/shutdown.
        """
        pass

    async def health_check(self) -> bool:
        """
        Performs quick diagnostic check (e.g. ping host, verify auth state).
        Returns True if healthy.
        """
        return True

    @abstractmethod
    async def collect(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Executes price data collection.
        Returns a dict structured as:
        {
            "gold": {
                "price": float,
                "raw_payload": str,
                ...
            },
            "silver": {
                "price": float,
                "raw_payload": str,
                ...
            }
        }
        Or None if fetch fails.
        """
        pass

    def validate(self, details: Dict[str, Any]) -> bool:
        """
        Performs structural data validation.
        """
        if not details or "price" not in details:
            return False
        price = details["price"]
        if not isinstance(price, (int, float)) or price <= 0:
            return False
        return True

    def normalize(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts the details dictionary into the normalized schema structure.
        """
        return details
