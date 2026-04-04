#!/usr/bin/env python3
"""
Main resilient API client that composes all resilience patterns.
Integrates retry policy, circuit breaker, task queue, and provider tracking.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional, Dict
from datetime import datetime, timezone

from .retry_policy import RetryPolicy, RetryPolicyError
from .circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState
from .task_queue import ExternalTaskQueue, TaskStatus
from .provider_tracker import ProviderStatusTracker

logger = logging.getLogger(__name__)


class ResilientAPIClient:
    """
    Composes all resilience patterns for robust external API interactions.

    Pattern:
    1. Check circuit breaker state
    2. If OPEN: enqueue to retry queue, return failure
    3. If CLOSED/HALF_OPEN: execute with retry policy
    4. On success: record metrics, return result
    5. On failure: enqueue to retry queue, record metrics
    """

    def __init__(self, db_path: str):
        """
        Initialize the resilient client.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.retry_policy = RetryPolicy(max_retries=3)
        self.circuit_breaker = CircuitBreaker(db_path)
        self.task_queue = ExternalTaskQueue(db_path)
        self.tracker = ProviderStatusTracker(db_path)

    async def call(
        self,
        provider: str,
        endpoint: str,
        func: Callable,
        *args,
        application_id: Optional[str] = None,
        task_type: Optional[str] = None,
        method: str = "GET",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make a resilient API call with all protections.

        Args:
            provider: Provider name (e.g., "sumsub", "opencorporates")
            endpoint: API endpoint
            func: Async function to call
            *args: Positional arguments for func
            application_id: Associated application ID
            task_type: Task type for queueing on failure
            method: HTTP method
            **kwargs: Keyword arguments for func

        Returns:
            Result dict: {"success": bool, "data": result, "error": str or None, ...}
        """
        start_time = time.time()
        circuit_state = None
        retry_count = 0
        status_code = None
        error_message = None

        try:
            # Check circuit breaker
            try:
                circuit_state = await self.circuit_breaker.get_state(provider)
                await self.circuit_breaker.check_call_allowed(provider)
            except CircuitBreakerError as e:
                circuit_state = CircuitState.OPEN
                latency_ms = int((time.time() - start_time) * 1000)

                # Record circuit open failure
                await self.tracker.record_failure(
                    provider=provider,
                    endpoint=endpoint,
                    method=method,
                    application_id=application_id,
                    latency_ms=latency_ms,
                    circuit_state=CircuitState.OPEN.value,
                    outcome="circuit_open",
                    error_message=str(e)
                )

                # Enqueue to retry queue
                if task_type and application_id:
                    payload = {
                        "endpoint": endpoint,
                        "args": str(args),
                        "kwargs": str(kwargs),
                        "method": method
                    }
                    await self.task_queue.enqueue(
                        task_type=task_type,
                        application_id=application_id,
                        provider=provider,
                        payload=payload
                    )

                return {
                    "success": False,
                    "error": f"Circuit breaker OPEN for {provider}",
                    "provider": provider,
                    "application_id": application_id,
                    "queued": True if task_type and application_id else False
                }

            # Execute with retry policy
            try:
                result = await self.retry_policy.execute(func, *args, **kwargs)
                retry_count = 0

                # Extract status code if present
                if hasattr(result, "status_code"):
                    status_code = result.status_code

                # Record success
                latency_ms = int((time.time() - start_time) * 1000)
                await self.circuit_breaker.record_success(provider)
                await self.tracker.record_success(
                    provider=provider,
                    endpoint=endpoint,
                    method=method,
                    application_id=application_id,
                    status_code=status_code or 200,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                    circuit_state=(circuit_state or CircuitState.CLOSED).value
                )

                logger.info(
                    f"Successful call to {provider}/{endpoint} in {latency_ms}ms (app={application_id})"
                )

                return {
                    "success": True,
                    "data": result,
                    "provider": provider,
                    "application_id": application_id,
                    "latency_ms": latency_ms,
                    "retry_count": retry_count
                }

            except RetryPolicyError as e:
                latency_ms = int((time.time() - start_time) * 1000)
                error_message = str(e)

                # Record failure
                await self.circuit_breaker.record_failure(provider)
                await self.tracker.record_failure(
                    provider=provider,
                    endpoint=endpoint,
                    method=method,
                    application_id=application_id,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                    circuit_state=(circuit_state or CircuitState.CLOSED).value,
                    outcome="failure",
                    error_message=error_message
                )

                # Enqueue to retry queue
                if task_type and application_id:
                    payload = {
                        "endpoint": endpoint,
                        "args": str(args),
                        "kwargs": str(kwargs),
                        "method": method,
                        "error": error_message
                    }
                    await self.task_queue.enqueue(
                        task_type=task_type,
                        application_id=application_id,
                        provider=provider,
                        payload=payload
                    )

                logger.error(
                    f"Failed call to {provider}/{endpoint} after retries: {error_message} (app={application_id})"
                )

                return {
                    "success": False,
                    "error": error_message,
                    "provider": provider,
                    "application_id": application_id,
                    "latency_ms": latency_ms,
                    "retry_count": retry_count,
                    "queued": True if task_type and application_id else False
                }

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            error_message = f"{type(e).__name__}: {str(e)}"

            logger.error(
                f"Unexpected error in resilient call to {provider}/{endpoint}: {error_message}",
                exc_info=True
            )

            # Record as failure
            await self.circuit_breaker.record_failure(provider)
            await self.tracker.record_failure(
                provider=provider,
                endpoint=endpoint,
                method=method,
                application_id=application_id,
                latency_ms=latency_ms,
                circuit_state=(circuit_state or CircuitState.CLOSED).value,
                outcome="failure",
                error_message=error_message
            )

            return {
                "success": False,
                "error": error_message,
                "provider": provider,
                "application_id": application_id,
                "latency_ms": latency_ms
            }

    async def process_retry_queue(self) -> Dict[str, Any]:
        """
        Process tasks in the retry queue (call periodically).

        Returns:
            Processing results: {"processed": int, "completed": int, "failed": int, "errors": []}
        """
        results = {
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "errors": []
        }

        try:
            ready_tasks = await self.task_queue.dequeue_ready()
            logger.info(f"Processing {len(ready_tasks)} ready tasks from queue")

            for task in ready_tasks:
                results["processed"] += 1

                try:
                    await self.task_queue.mark_processing(task.id)

                    # TODO: Implement actual task execution logic here
                    # For now, just mark as failed to re-queue
                    await self.task_queue.mark_failed(
                        task.id,
                        "Queue processing not yet implemented"
                    )

                except Exception as e:
                    results["failed"] += 1
                    error_msg = f"Task {task.id} processing failed: {str(e)}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg, exc_info=True)

                    try:
                        await self.task_queue.mark_failed(task.id, str(e))
                    except Exception as inner_e:
                        logger.error(f"Failed to mark task {task.id} as failed: {inner_e}")

            return results

        except Exception as e:
            logger.error(f"Error processing retry queue: {e}", exc_info=True)
            results["errors"].append(str(e))
            return results

    async def get_metrics(self) -> Dict[str, Any]:
        """
        Get comprehensive metrics for monitoring.

        Returns:
            Metrics dict containing:
            {
                "circuit_breakers": {...},
                "provider_statuses": {...},
                "queue_stats": {...},
                "health": {...},
                "timestamp": "2025-03-17T10:00:00Z"
            }
        """
        return {
            "circuit_breakers": await self.circuit_breaker.get_all_states(),
            "provider_statuses": await self.tracker.get_all_statuses(),
            "queue_stats": await self.task_queue.get_queue_stats(),
            "health": await self.tracker.get_health_summary(),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
