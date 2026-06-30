import asyncio
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import RawTick

logger = logging.getLogger("EventBus")

class EventBus:
    def __init__(self):
        # Bound the queue size to 1000 to manage backpressure and protect system memory
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.workers: List[asyncio.Task] = []
        self._running = False
        self._consensus_callback = None

    def register_consensus_callback(self, callback):
        """
        Register a callback function to handle raw ticks (usually the consensus engine).
        """
        self._consensus_callback = callback

    async def publish_raw_tick(self, commodity: str, price: float, source: str, latency_ms: int, raw_payload: str):
        """
        Publish a raw tick to the event bus. Put it in the queue for async processing.
        """
        event = {
            "commodity": commodity.lower(),
            "price": price,
            "source": source,
            "latency_ms": latency_ms,
            "timestamp": datetime.now(timezone.utc),
            "raw_payload": raw_payload
        }
        await self.queue.put(event)

    async def start_workers(self):
        self._running = True
        # Spawn 3 concurrent worker tasks
        for i in range(3):
            task = asyncio.create_task(self._worker_loop(i))
            self.workers.append(task)
        logger.info(f"EventBus workers started ({len(self.workers)} loops).")

    async def stop_workers(self):
        self._running = False
        logger.info("Stopping EventBus workers...")
        
        # Add None values to the queue to unblock workers waiting on get()
        for _ in range(len(self.workers)):
            await self.queue.put(None)
            
        # Wait for workers to finish (with a timeout of 5s)
        try:
            await asyncio.wait_for(asyncio.gather(*self.workers, return_exceptions=True), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("EventBus worker shutdown timed out, cancelling tasks.")
            for task in self.workers:
                task.cancel()
        
        self.workers.clear()
        logger.info("EventBus workers stopped.")

    async def _worker_loop(self, worker_id: int):
        while self._running:
            event = await self.queue.get()
            if event is None:
                self.queue.task_done()
                break
                
            try:
                # 1. Log Raw Tick to database
                await self._save_raw_tick(event)
                
                # 2. Forward to Consensus Engine callback
                if self._consensus_callback:
                    # Await consensus evaluation sequentially to preserve strict ordering and catch errors
                    await self._consensus_callback(event)
            except Exception as e:
                logger.error(f"EventBus Worker {worker_id} error processing event: {e}", exc_info=True)
            finally:
                self.queue.task_done()

    async def _save_raw_tick(self, event: Dict[str, Any]):
        """
        Saves raw tick information to PostgreSQL for audit replay capabilities.
        """
        # Run blocking SQLAlchemy operation in thread pool to not block async loop
        def db_write():
            db = SessionLocal()
            try:
                raw_model = RawTick(
                    commodity=event["commodity"],
                    price=event["price"],
                    source=event["source"],
                    latency_ms=event["latency_ms"],
                    timestamp=event["timestamp"],
                    raw_payload=event["raw_payload"]
                )
                db.add(raw_model)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to save raw tick to DB: {e}")
            finally:
                db.close()
                
        await asyncio.to_thread(db_write)

event_bus = EventBus()
