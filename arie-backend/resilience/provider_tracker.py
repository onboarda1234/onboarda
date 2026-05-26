#!/usr/bin/env python3
"""
Tracks per-provider metrics for monitoring API health and resilience.
Records success/failure rates, latencies, and circuit breaker states.
"""

import asyncio
import logging
import aiosqlite
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from statistics import mean

logger = logging.getLogger(__name__)


class ProviderStatusTracker:
    """
    Tracks metrics for external API providers to monitor health and performance.
    """

    def __init__(self, db_path: str):
        """
        Initialize the provider tracker.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path

    async def record_success(
        self,
        provider: str,
        endpoint: str,
        method: str = "GET",
        application_id: Optional[str] = None,
        status_code: int = 200,
        latency_ms: int = 0,
        retry_count: int = 0,
        circuit_state: str = "CLOSED"
    ) -> None:
        """
        Record a successful API call.

        Args:
            provider: Provider name (e.g., "sumsub", "opencorporates")
            endpoint: API endpoint called
            method: HTTP method
            application_id: Associated application ID
            status_code: HTTP status code
            latency_ms: Request latency in milliseconds
            retry_count: Number of retries before success
            circuit_state: Circuit breaker state at time of call
        """
        async with aiosqlite.connect(self.db_path) as db:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            await db.execute(
                """
                INSERT INTO external_api_attempts
                (provider, endpoint, method, application_id, status_code, latency_ms,
                 retry_count, circuit_state, outcome, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    endpoint,
                    method,
                    application_id,
                    status_code,
                    latency_ms,
                    retry_count,
                    circuit_state,
                    "success",
                    None,
                    created_at
                )
            )
            await db.commit()

    async def record_failure(
        self,
        provider: str,
        endpoint: str,
        method: str = "GET",
        application_id: Optional[str] = None,
        status_code: Optional[int] = None,
        latency_ms: int = 0,
        retry_count: int = 0,
        circuit_state: str = "CLOSED",
        outcome: str = "failure",
        error_message: Optional[str] = None
    ) -> None:
        """
        Record a failed API call.

        Args:
            provider: Provider name
            endpoint: API endpoint called
            method: HTTP method
            application_id: Associated application ID
            status_code: HTTP status code (if applicable)
            latency_ms: Request latency in milliseconds
            retry_count: Number of retries
            circuit_state: Circuit breaker state
            outcome: Outcome type (failure, timeout, circuit_open)
            error_message: Error description
        """
        async with aiosqlite.connect(self.db_path) as db:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            await db.execute(
                """
                INSERT INTO external_api_attempts
                (provider, endpoint, method, application_id, status_code, latency_ms,
                 retry_count, circuit_state, outcome, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    endpoint,
                    method,
                    application_id,
                    status_code,
                    latency_ms,
                    retry_count,
                    circuit_state,
                    outcome,
                    error_message,
                    created_at
                )
            )
            await db.commit()

    async def get_provider_status(self, provider: str) -> Dict[str, Any]:
        """
        Get status and metrics for a specific provider.

        Args:
            provider: Provider name

        Returns:
            Dictionary with provider metrics:
            {
                "provider": str,
                "total_calls": int,
                "successful_calls": int,
                "failed_calls": int,
                "success_rate": float (0-1),
                "avg_latency_ms": float,
                "last_success": "2025-03-17T10:00:00Z" or null,
                "last_failure": "2025-03-17T10:00:00Z" or null,
                "last_call": "2025-03-17T10:00:00Z" or null,
                "endpoints": {endpoint: {counts, latencies}, ...}
            }
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Overall stats
            cursor = await db.execute(
                """
                SELECT COUNT(*), SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN outcome != 'success' THEN 1 ELSE 0 END),
                       AVG(latency_ms), MAX(created_at)
                FROM external_api_attempts
                WHERE provider = ?
                """,
                (provider,)
            )
            row = await cursor.fetchone()

            if not row or row[0] == 0:
                return {
                    "provider": provider,
                    "total_calls": 0,
                    "successful_calls": 0,
                    "failed_calls": 0,
                    "success_rate": 0.0,
                    "avg_latency_ms": 0,
                    "last_success": None,
                    "last_failure": None,
                    "last_call": None,
                    "endpoints": {}
                }

            total_calls = row[0]
            successful_calls = row[1] or 0
            failed_calls = row[2] or 0
            avg_latency = row[3] or 0
            last_call = row[4]

            success_rate = successful_calls / total_calls if total_calls > 0 else 0

            # Last success/failure
            cursor = await db.execute(
                """
                SELECT MAX(created_at) FROM external_api_attempts
                WHERE provider = ? AND outcome = 'success'
                """,
                (provider,)
            )
            last_success = (await cursor.fetchone())[0]

            cursor = await db.execute(
                """
                SELECT MAX(created_at) FROM external_api_attempts
                WHERE provider = ? AND outcome != 'success'
                """,
                (provider,)
            )
            last_failure = (await cursor.fetchone())[0]

            # Per-endpoint stats
            cursor = await db.execute(
                """
                SELECT endpoint, COUNT(*),
                       SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END),
                       AVG(latency_ms)
                FROM external_api_attempts
                WHERE provider = ?
                GROUP BY endpoint
                """,
                (provider,)
            )
            endpoint_rows = await cursor.fetchall()

            endpoints = {}
            for row in endpoint_rows:
                endpoint = row[0]
                calls = row[1]
                successes = row[2] or 0
                avg_latency = row[3] or 0

                endpoints[endpoint] = {
                    "calls": calls,
                    "successes": successes,
                    "success_rate": successes / calls if calls > 0 else 0,
                    "avg_latency_ms": round(avg_latency, 2)
                }

            return {
                "provider": provider,
                "total_calls": total_calls,
                "successful_calls": successful_calls,
                "failed_calls": failed_calls,
                "success_rate": round(success_rate, 3),
                "avg_latency_ms": round(avg_latency, 2),
                "last_success": last_success,
                "last_failure": last_failure,
                "last_call": last_call,
                "endpoints": endpoints
            }

    async def get_all_statuses(self) -> Dict[str, Dict[str, Any]]:
        """
        Get status for all providers.

        Returns:
            Dictionary mapping provider names to their status dicts
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT DISTINCT provider FROM external_api_attempts"
            )
            rows = await cursor.fetchall()
            providers = [row[0] for row in rows]

        statuses = {}
        for provider in providers:
            statuses[provider] = await self.get_provider_status(provider)

        return statuses

    async def get_recent_failures(
        self,
        provider: Optional[str] = None,
        minutes: int = 60
    ) -> list:
        """
        Get recent failures for monitoring/alerting.

        Args:
            provider: Specific provider to check, or None for all
            minutes: Look back this many minutes

        Returns:
            List of failure records
        """
        async with aiosqlite.connect(self.db_path) as db:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

            if provider:
                cursor = await db.execute(
                    """
                    SELECT provider, endpoint, method, status_code, outcome, error_message, created_at
                    FROM external_api_attempts
                    WHERE provider = ? AND outcome != 'success' AND created_at >= ?
                    ORDER BY created_at DESC
                    """,
                    (provider, cutoff)
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT provider, endpoint, method, status_code, outcome, error_message, created_at
                    FROM external_api_attempts
                    WHERE outcome != 'success' AND created_at >= ?
                    ORDER BY provider, created_at DESC
                    """,
                    (cutoff,)
                )

            rows = await cursor.fetchall()

            return [
                {
                    "provider": row[0],
                    "endpoint": row[1],
                    "method": row[2],
                    "status_code": row[3],
                    "outcome": row[4],
                    "error_message": row[5],
                    "created_at": row[6]
                }
                for row in rows
            ]

    async def get_health_summary(self) -> Dict[str, Any]:
        """
        Get overall health summary across all providers.

        Returns:
            Health summary with aggregated metrics
        """
        statuses = await self.get_all_statuses()

        total_calls = sum(s["total_calls"] for s in statuses.values())
        total_successes = sum(s["successful_calls"] for s in statuses.values())
        total_failures = sum(s["failed_calls"] for s in statuses.values())

        if total_calls == 0:
            overall_success_rate = 0.0
            avg_latency = 0
        else:
            overall_success_rate = total_successes / total_calls
            latencies = [s["avg_latency_ms"] for s in statuses.values() if s["avg_latency_ms"] > 0]
            avg_latency = mean(latencies) if latencies else 0

        # Identify unhealthy providers
        unhealthy = [
            {
                "provider": name,
                "success_rate": status["success_rate"],
                "recent_failures": status["failed_calls"]
            }
            for name, status in statuses.items()
            if status["success_rate"] < 0.9  # Less than 90% success rate
        ]

        return {
            "total_providers": len(statuses),
            "total_calls": total_calls,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "overall_success_rate": round(overall_success_rate, 3),
            "avg_latency_ms": round(avg_latency, 2),
            "unhealthy_providers": unhealthy,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
