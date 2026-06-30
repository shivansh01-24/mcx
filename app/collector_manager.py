import os
import sys
import importlib
import asyncio
import logging
import time
import inspect
from typing import Dict, Any, List, Type, Optional
from datetime import datetime, timezone
from app.config import settings
from app.database import SessionLocal
from app.models import CollectorMetricModel
from app.event_bus import event_bus

logger = logging.getLogger("CollectorManager")

# Base path for collectors
COLLECTORS_DIR = os.path.join(os.path.dirname(__file__), "collectors")

class CollectorManager:
    def __init__(self):
        self.collectors: Dict[str, Any] = {}          # Instantiated collector objects {name: instance}
        self.collector_tasks: Dict[str, asyncio.Task] = {}  # Running loop tasks {name: Task}
        self.collector_files: Dict[str, float] = {}   # File modifications {filename: mtime}
        self.metrics: Dict[str, Dict[str, Any]] = {}  # In-memory metrics tracking
        self._running = False
        self._reload_task = None

    async def start(self):
        self._running = True
        logger.info("Starting CollectorManager...")
        
        # Ensure collectors directory exists
        os.makedirs(COLLECTORS_DIR, exist_ok=True)

        # 1. Initial scan and load
        await self.scan_collectors_dir()

        # 2. Start the directory watcher / hot reloader
        self._reload_task = asyncio.create_task(self._hot_reloader_loop())
        logger.info("CollectorManager started.")

    async def stop(self):
        self._running = False
        logger.info("Stopping CollectorManager...")
        
        if self._reload_task:
            self._reload_task.cancel()

        # Stop all collector tasks
        for name in list(self.collectors.keys()):
            await self.unload_collector(name)
            
        logger.info("CollectorManager stopped.")

    async def scan_collectors_dir(self):
        """
        Scans the collectors directory, registers new collectors, reloads modified ones,
        and unloads deleted ones.
        """
        if not os.path.exists(COLLECTORS_DIR):
            return

        current_files = {}
        for filename in os.listdir(COLLECTORS_DIR):
            if filename.endswith(".py") and filename != "__init__.py" and filename != "base.py":
                filepath = os.path.join(COLLECTORS_DIR, filename)
                mtime = os.path.getmtime(filepath)
                current_files[filename] = mtime

        # 1. Detect deleted collectors
        for filename in list(self.collector_files.keys()):
            if filename not in current_files:
                name = filename[:-3] # remove '.py'
                logger.info(f"Collector file deleted: {filename}. Unloading...")
                await self.unload_collector(name)
                del self.collector_files[filename]

        # 2. Detect new or modified collectors
        for filename, mtime in current_files.items():
            name = filename[:-3]
            old_mtime = self.collector_files.get(filename)
            
            if old_mtime is None:
                # New collector
                logger.info(f"New collector file detected: {filename}. Loading...")
                if await self.load_collector(name, filename):
                    self.collector_files[filename] = mtime
            elif mtime > old_mtime:
                # Modified collector
                logger.info(f"Modified collector file detected: {filename}. Reloading...")
                await self.unload_collector(name)
                if await self.load_collector(name, filename):
                    self.collector_files[filename] = mtime

    async def load_collector(self, name: str, filename: str) -> bool:
        """
        Dynamically imports the module, validates base class, manifest format,
        initializes metrics, and runs it inside an isolated loop.
        """
        try:
            filepath = os.path.join(COLLECTORS_DIR, filename)
            # Add app.collectors path to sys.path to allow relative imports
            sys.path.insert(0, os.path.dirname(COLLECTORS_DIR))
            
            # Use importlib to load module dynamically
            spec = importlib.util.spec_from_file_location(f"app.collectors.{name}", filepath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find subclasses of BaseCollector
            from app.collectors.base import BaseCollector
            collector_class: Type[BaseCollector] = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if inspect.isclass(attr) and issubclass(attr, BaseCollector) and attr != BaseCollector:
                    collector_class = attr
                    break

            if not collector_class:
                logger.warning(f"No BaseCollector subclass found in {filename}. Skipping.")
                return False

            # Validate MANIFEST
            manifest = getattr(collector_class, "MANIFEST", None)
            if not manifest:
                logger.warning(f"Collector {name} is missing MANIFEST metadata. Skipping.")
                return False
                
            required_keys = ["name", "version", "priority", "collector_type", "supported_commodities"]
            if not all(k in manifest for k in required_keys):
                logger.warning(f"Collector {name} MANIFEST is missing required keys {required_keys}. Skipping.")
                return False

            # Check min platform version
            min_platform = manifest.get("min_platform_version", "1.0.0")
            if min_platform > settings.platform.version:
                logger.warning(f"Collector {name} requires platform version {min_platform} (current: {settings.platform.version}). Skipping.")
                return False

            # Skip experimental collectors in production
            if manifest.get("experimental", False):
                logger.info(f"Collector '{name}' is marked as Experimental and is skipped by default.")
                return False

            # Instantiate collector
            instance = collector_class()
            self.collectors[name] = instance
            
            # Initialize metrics structure
            self.metrics[name] = {
                "avg_latency": 0.0,
                "success_rate": 100.0,
                "failure_rate": 0.0,
                "timeout_rate": 0.0,
                "consecutive_failures": 0,
                "circuit_breaker_status": "CLOSED",
                "last_successful_update": None,
                "total_calls": 0,
                "total_failures": 0,
                "total_timeouts": 0,
                "total_parsing_failures": 0,
                "last_active": None,
                "score": 0.0
            }

            # Start database metrics record
            await self._init_db_metrics(name)

            # Spawn isolated task loop
            task = asyncio.create_task(self._run_collector_isolated_loop(name))
            self.collector_tasks[name] = task
            
            logger.info(f"Collector '{name}' loaded and running successfully (v{manifest['version']}).")
            return True
        except Exception as e:
            logger.error(f"Failed to load collector {name}: {e}", exc_info=True)
            return False

    async def unload_collector(self, name: str):
        """
        Safely stops the isolated loop and unloads the module instance.
        """
        logger.info(f"Unloading collector '{name}'...")
        
        # Cancel the task
        task = self.collector_tasks.get(name)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            del self.collector_tasks[name]

        # Call Stop on the collector
        instance = self.collectors.get(name)
        if instance:
            try:
                await instance.stop()
            except Exception as e:
                logger.error(f"Error stopping collector '{name}' instance: {e}")
            del self.collectors[name]

        # Clean metrics
        if name in self.metrics:
            del self.metrics[name]
            
        # Clean Python module cache to prevent memory leaks during hot swapping
        if f"app.collectors.{name}" in sys.modules:
            del sys.modules[f"app.collectors.{name}"]
            
        logger.info(f"Collector '{name}' unloaded.")

    async def _hot_reloader_loop(self):
        """
        Periodically checks files for updates.
        """
        while self._running:
            try:
                await asyncio.sleep(5.0)
                await self.scan_collectors_dir()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in hot reloader loop: {e}")

    async def _run_collector_isolated_loop(self, name: str):
        """
        Runs the collector's start and collection loops in isolation.
        Restarts only the crashed collector with exponential backoff on exceptions.
        """
        collector = self.collectors[name]
        manifest = collector.MANIFEST
        polling_interval = manifest.get("polling_interval", 10)
        
        backoff_delay = settings.retry.base_delay_seconds
        
        # Call start once
        try:
            await collector.start()
        except Exception as e:
            logger.error(f"Collector '{name}' failed during start(): {e}")
            self.record_failure(name, "startup_error")

        while self._running:
            # Check if collector has been disabled manually
            if not collector.is_active:
                await asyncio.sleep(1.0)
                continue
                
            try:
                # If circuit breaker is OPEN, skip collection and test health
                metrics = self.metrics[name]
                if metrics["circuit_breaker_status"] == "OPEN":
                    last_active = metrics["last_active"]
                    if last_active:
                        cooldown_elapsed = (datetime.now(timezone.utc) - last_active).total_seconds()
                        if cooldown_elapsed >= settings.circuit_breaker.recovery_timeout_seconds:
                            logger.info(f"Circuit breaker for '{name}' cooldown complete. Attempting HALF-OPEN probe.")
                            metrics["circuit_breaker_status"] = "HALF-OPEN"
                        else:
                            await asyncio.sleep(2.0)
                            continue

                # Run Health check
                t_start = time.time()
                is_healthy = await collector.health_check()
                if not is_healthy:
                    raise Exception("Health check reported unhealthy.")

                # Run Collection
                data = await asyncio.wait_for(
                    collector.collect(),
                    timeout=settings.retry.timeout_seconds
                )
                latency = int((time.time() - t_start) * 1000)

                # Process results
                if data:
                    # Validate and normalize
                    for commodity, details in data.items():
                        if not collector.validate(details):
                            self.record_failure(name, "validation_error")
                            continue
                            
                        # Publish Raw tick to EventBus for DB logging and Consensus processing
                        await event_bus.publish_raw_tick(
                            commodity=commodity,
                            price=details["price"],
                            source=name,
                            latency_ms=latency,
                            raw_payload=details.get("raw_payload", str(details))
                        )

                    # Update successful metrics
                    self.record_success(name, latency)
                    backoff_delay = settings.retry.base_delay_seconds  # reset backoff
                else:
                    self.record_failure(name, "empty_data")

            except asyncio.TimeoutError:
                logger.warning(f"Collector '{name}' timed out during fetch.")
                self.record_failure(name, "timeout")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in isolated loop of collector '{name}': {e}")
                self.record_failure(name, "execution_error")
                
                # Apply exponential backoff restart delay
                logger.info(f"Restarting isolated loop for '{name}' in {backoff_delay:.2f} seconds...")
                await asyncio.sleep(backoff_delay)
                backoff_delay = min(settings.retry.max_delay_seconds, backoff_delay * 2)
                continue

            # Wait for next poll interval if using REST/HTML. WebSocket collectors do not poll.
            if manifest["collector_type"] == "WebSocket":
                # WebSocket streams updates via event push rather than polling
                await asyncio.sleep(5.0)
            else:
                await asyncio.sleep(polling_interval)

    def record_success(self, name: str, latency_ms: int):
        metrics = self.metrics[name]
        metrics["total_calls"] += 1
        metrics["consecutive_failures"] = 0
        metrics["last_active"] = datetime.now(timezone.utc)
        metrics["last_successful_update"] = datetime.now(timezone.utc)
        
        # Calculate running average of latency
        old_avg = metrics["avg_latency"]
        calls = metrics["total_calls"]
        metrics["avg_latency"] = old_avg + (latency_ms - old_avg) / min(calls, 100) # rolling average last 100 calls
        
        # If circuit breaker was HALF-OPEN, close it
        if metrics["circuit_breaker_status"] in ["OPEN", "HALF-OPEN"]:
            logger.info(f"Collector '{name}' recovered. Closing circuit breaker.")
            metrics["circuit_breaker_status"] = "CLOSED"

        # Update success rate
        total = metrics["total_calls"] + metrics["total_failures"]
        metrics["success_rate"] = (metrics["total_calls"] / total) * 100.0
        
        # Trigger DB write
        asyncio.create_task(self._update_db_metrics(name))

    def record_failure(self, name: str, failure_type: str):
        metrics = self.metrics[name]
        metrics["total_failures"] += 1
        metrics["consecutive_failures"] += 1
        metrics["last_active"] = datetime.now(timezone.utc)

        if failure_type == "timeout":
            metrics["total_timeouts"] += 1
        elif failure_type in ["validation_error", "outlier"]:
            metrics["total_parsing_failures"] += 1

        # Check circuit breaker
        if metrics["consecutive_failures"] >= settings.circuit_breaker.failure_threshold:
            if metrics["circuit_breaker_status"] != "OPEN":
                logger.critical(f"Collector '{name}' exceeded consecutive failures ({metrics['consecutive_failures']}). TRIPPING CIRCUIT BREAKER.")
                metrics["circuit_breaker_status"] = "OPEN"

        # Update success rate
        total = metrics["total_calls"] + metrics["total_failures"]
        metrics["success_rate"] = (metrics["total_calls"] / total) * 100.0
        metrics["failure_rate"] = (metrics["total_failures"] / total) * 100.0
        metrics["timeout_rate"] = (metrics["total_timeouts"] / total) * 100.0
        
        # Trigger DB write
        asyncio.create_task(self._update_db_metrics(name))

    def calculate_ranks(self):
        """
        Dynamically evaluates and ranks collectors based on weights defined in config.yaml.
        """
        weights = settings.ranking.weights
        
        for name, collector in self.collectors.items():
            metrics = self.metrics[name]
            manifest = collector.MANIFEST
            
            # Base priority score (priority mapping 1->100, 2->75, 3->50, etc.)
            priority_val = manifest.get("priority", 3)
            priority_score = 100.0 / priority_val if priority_val > 0 else 10.0
            
            success_rate = metrics["success_rate"]
            latency_ms = metrics["avg_latency"]
            consec_failures = metrics["consecutive_failures"]
            cb_status = metrics["circuit_breaker_status"]
            
            # Latency penalty: 0ms -> 0 penalty, 500ms+ -> max penalty
            latency_penalty = min(100.0, (latency_ms / 500.0) * 100.0)
            
            # Compute score
            score = (
                (weights.priority * (priority_score / 100.0)) +
                (weights.success_rate * (success_rate / 100.0)) -
                (weights.latency * (latency_penalty / 100.0)) -
                (weights.failures * (consec_failures / 5.0))
            )
            
            # If circuit breaker is open, set score to 0
            if cb_status == "OPEN":
                score = -100.0

            metrics["score"] = round(max(-100.0, score), 2)

    def get_collector(self, name: str) -> Optional[Any]:
        return self.collectors.get(name)

    def is_collector_healthy(self, name: str) -> bool:
        if name not in self.metrics:
            return False
        return self.metrics[name]["circuit_breaker_status"] != "OPEN"

    def get_best_collector_for(self, commodity: str) -> Optional[str]:
        """
        Returns the highest-scoring healthy collector supporting the commodity.
        """
        self.calculate_ranks()
        best_name = None
        best_score = -999.0
        
        for name, instance in self.collectors.items():
            manifest = instance.MANIFEST
            if commodity.lower() in [c.lower() for c in manifest["supported_commodities"]]:
                metrics = self.metrics[name]
                if metrics["circuit_breaker_status"] != "OPEN" and metrics["score"] > best_score:
                    best_score = metrics["score"]
                    best_name = name
        return best_name

    async def _init_db_metrics(self, name: str):
        def db_write():
            db = SessionLocal()
            try:
                metric = db.query(CollectorMetricModel).filter(
                    CollectorMetricModel.collector_name == name
                ).first()
                if not metric:
                    new_metric = CollectorMetricModel(
                        collector_name=name,
                        avg_latency=0.0,
                        success_rate=100.0,
                        failure_rate=0.0,
                        timeout_rate=0.0,
                        consecutive_failures=0,
                        circuit_breaker_status="CLOSED"
                    )
                    db.add(new_metric)
                    db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Error initializing DB metrics for {name}: {e}")
            finally:
                db.close()
        await asyncio.to_thread(db_write)

    async def _update_db_metrics(self, name: str):
        metrics = self.metrics.get(name)
        if not metrics:
            return
            
        def db_write():
            db = SessionLocal()
            try:
                metric = db.query(CollectorMetricModel).filter(
                    CollectorMetricModel.collector_name == name
                ).first()
                if metric:
                    metric.avg_latency = metrics["avg_latency"]
                    metric.success_rate = metrics["success_rate"]
                    metric.failure_rate = metrics.get("failure_rate", 0.0)
                    metric.timeout_rate = metrics.get("timeout_rate", 0.0)
                    metric.consecutive_failures = metrics["consecutive_failures"]
                    metric.circuit_breaker_status = metrics["circuit_breaker_status"]
                    metric.last_successful_update = metrics["last_successful_update"]
                    metric.total_calls = metrics["total_calls"]
                    metric.total_failures = metrics["total_failures"]
                    metric.total_timeouts = metrics["total_timeouts"]
                    metric.total_parsing_failures = metrics["total_parsing_failures"]
                    db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Error updating DB metrics for {name}: {e}")
            finally:
                db.close()
        await asyncio.to_thread(db_write)

collector_manager = CollectorManager()
