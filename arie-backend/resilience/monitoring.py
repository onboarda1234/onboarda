#!/usr/bin/env python3
"""
Monitoring endpoints for resilience metrics and health checks.
Provides visibility into circuit breaker states, queue status, and provider health.
"""

import logging
import json
import tornado.web
from typing import Optional

from .resilient_client import ResilientAPIClient
from .circuit_breaker import CircuitBreaker
from .task_queue import ExternalTaskQueue

logger = logging.getLogger(__name__)


class BaseResilienceHandler(tornado.web.RequestHandler):
    """Base handler for resilience endpoints with common utilities."""

    def set_default_headers(self):
        """Set CORS and content type headers."""
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")

    def options(self):
        """Handle CORS preflight requests."""
        self.set_status(204)
        self.finish()

    def get_db_path(self) -> str:
        """Get database path from application."""
        return self.application.settings.get("db_path", "./arie_finance.db")

    def write_json(self, data: dict):
        """Write JSON response."""
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(data, default=str))


class ResilienceMetricsHandler(BaseResilienceHandler):
    """
    GET /api/resilience/metrics
    Returns comprehensive metrics for all providers, queues, and circuits.
    """

    async def get(self):
        """Get resilience metrics."""
        try:
            db_path = self.get_db_path()
            client = ResilientAPIClient(db_path)

            metrics = await client.get_metrics()

            self.write_json({
                "success": True,
                "data": metrics
            })

        except Exception as e:
            logger.error(f"Error getting metrics: {e}", exc_info=True)
            self.set_status(500)
            self.write_json({
                "success": False,
                "error": str(e)
            })


class ResilienceQueueHandler(BaseResilienceHandler):
    """
    GET /api/resilience/queue?status=pending&provider=sumsub
    Returns queue contents with optional filtering.
    """

    async def get(self):
        """Get queue contents."""
        try:
            db_path = self.get_db_path()
            queue = ExternalTaskQueue(db_path)

            # Get filters
            status = self.get_argument("status", None)
            provider = self.get_argument("provider", None)

            # Get queue stats
            stats = await queue.get_queue_stats()

            # Get ready tasks
            ready_tasks = await queue.dequeue_ready()

            # Format task data
            tasks = []
            for task in ready_tasks:
                if status and task.status.value != status:
                    continue
                if provider and task.provider != provider:
                    continue

                tasks.append({
                    "id": task.id,
                    "task_type": task.task_type,
                    "application_id": task.application_id,
                    "provider": task.provider,
                    "attempt_count": task.attempt_count,
                    "max_retries": task.max_retries,
                    "next_retry_at": task.next_retry_at.isoformat() + "Z",
                    "status": task.status.value,
                    "last_error": task.last_error,
                    "created_at": task.created_at.isoformat() + "Z"
                })

            self.write_json({
                "success": True,
                "stats": stats,
                "ready_count": len(tasks),
                "tasks": tasks
            })

        except Exception as e:
            logger.error(f"Error getting queue: {e}", exc_info=True)
            self.set_status(500)
            self.write_json({
                "success": False,
                "error": str(e)
            })


class RetryQueueTaskHandler(BaseResilienceHandler):
    """
    POST /api/resilience/queue/{task_id}/retry
    Manually trigger retry of a specific task.
    """

    async def post(self, task_id: str):
        """Retry a task."""
        try:
            db_path = self.get_db_path()
            queue = ExternalTaskQueue(db_path)

            # Get the task
            task = await queue.get_task(int(task_id))

            if not task:
                self.set_status(404)
                self.write_json({
                    "success": False,
                    "error": f"Task {task_id} not found"
                })
                return

            # Reset for immediate retry
            from datetime import datetime
            now = datetime.utcnow().isoformat() + "Z"

            # Mark as pending with immediate next_retry_at
            # This is a bit hacky - we need a proper method for this
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    """
                    UPDATE external_retry_queue
                    SET status = 'pending', next_retry_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, int(task_id))
                )
                await db.commit()

            logger.info(f"Manually triggered retry for task {task_id}")

            self.write_json({
                "success": True,
                "message": f"Task {task_id} queued for immediate retry",
                "task_id": task_id
            })

        except Exception as e:
            logger.error(f"Error retrying task: {e}", exc_info=True)
            self.set_status(500)
            self.write_json({
                "success": False,
                "error": str(e)
            })


class CircuitBreakerResetHandler(BaseResilienceHandler):
    """
    POST /api/resilience/circuit/{provider}/reset
    Manually reset circuit breaker for a provider.
    """

    async def post(self, provider: str):
        """Reset circuit breaker."""
        try:
            db_path = self.get_db_path()
            breaker = CircuitBreaker(db_path)

            await breaker.reset(provider)

            logger.info(f"Manually reset circuit breaker for {provider}")

            self.write_json({
                "success": True,
                "message": f"Circuit breaker reset for {provider}",
                "provider": provider,
                "new_state": "CLOSED"
            })

        except Exception as e:
            logger.error(f"Error resetting circuit breaker: {e}", exc_info=True)
            self.set_status(500)
            self.write_json({
                "success": False,
                "error": str(e)
            })


class ResilienceHealthHandler(BaseResilienceHandler):
    """
    GET /api/resilience/health
    Overall health check of the resilience layer.
    """

    async def get(self):
        """Get health status."""
        try:
            db_path = self.get_db_path()
            client = ResilientAPIClient(db_path)
            tracker = client.tracker

            health = await tracker.get_health_summary()
            queue_stats = await client.task_queue.get_queue_stats()
            circuit_states = await client.circuit_breaker.get_all_states()

            # Determine overall health
            overall_status = "healthy"

            if health["overall_success_rate"] < 0.8:
                overall_status = "degraded"

            if health["overall_success_rate"] < 0.5 or queue_stats["dead_letter_count"] > 10:
                overall_status = "critical"

            if len(circuit_states) > 0:
                open_circuits = sum(1 for state in circuit_states.values() if state == "OPEN")
                if open_circuits > 0:
                    overall_status = "degraded" if overall_status == "healthy" else overall_status

            self.write_json({
                "success": True,
                "status": overall_status,
                "health": health,
                "queue_stats": queue_stats,
                "circuit_states": circuit_states,
                "timestamp": health["timestamp"]
            })

        except Exception as e:
            logger.error(f"Error checking health: {e}", exc_info=True)
            self.set_status(500)
            self.write_json({
                "success": False,
                "error": str(e)
            })


class ResilienceStatusHandler(BaseResilienceHandler):
    """
    GET /api/resilience/status
    Quick status check (lightweight).
    """

    async def get(self):
        """Get quick status."""
        try:
            db_path = self.get_db_path()
            client = ResilientAPIClient(db_path)

            metrics = await client.get_metrics()

            # Count open circuits
            open_circuits = [
                p for p, state in metrics["circuit_breakers"].items()
                if state == "OPEN"
            ]

            # Count dead letter queue items
            dead_count = metrics["queue_stats"].get("dead_letter_count", 0)

            status = "ok"
            if len(open_circuits) > 0 or dead_count > 0:
                status = "warning"
            if len(open_circuits) > 1 or dead_count > 10:
                status = "critical"

            self.write_json({
                "status": status,
                "open_circuits": open_circuits,
                "dead_letters": dead_count,
                "queue_pending": metrics["queue_stats"].get("by_status", {}).get("pending", 0)
            })

        except Exception as e:
            logger.error(f"Error getting status: {e}", exc_info=True)
            self.set_status(500)
            self.write_json({
                "success": False,
                "error": str(e)
            })


def get_resilience_routes():
    """
    Get Tornado routes for resilience monitoring.

    Returns:
        List of (pattern, handler) tuples
    """
    return [
        (r"/api/resilience/metrics", ResilienceMetricsHandler),
        (r"/api/resilience/queue", ResilienceQueueHandler),
        (r"/api/resilience/queue/(\d+)/retry", RetryQueueTaskHandler),
        (r"/api/resilience/circuit/([^/]+)/reset", CircuitBreakerResetHandler),
        (r"/api/resilience/health", ResilienceHealthHandler),
        (r"/api/resilience/status", ResilienceStatusHandler),
    ]
