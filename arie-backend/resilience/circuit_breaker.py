#!/usr/bin/env python3
"""
Circuit breaker pattern implementation for external API resilience.
States: CLOSED (normal), OPEN (failed), HALF_OPEN (testing recovery)
"""

import asyncio
import logging
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Failing, rejecting calls
    HALF_OPEN = "HALF_OPEN"  # Testing recovery


class CircuitBreakerError(Exception):
    """Raised when circuit is OPEN"""
    pass


class CircuitBreaker:
    """
    Thread-safe (asyncio-safe) circuit breaker for external API providers.

    Configuration:
    - Open after: 5 failures within 5 minutes
    - Half-open cooldown: 2 minutes
    - Half-open probe: 1 request allowed, success -> CLOSED, failure -> OPEN
    """

    FAILURE_THRESHOLD = 5
    FAILURE_WINDOW_MINUTES = 5
    COOLDOWN_MINUTES = 2

    def __init__(self, db_path: str):
        """
        Initialize circuit breaker.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self._locks = {}  # Per-provider locks for thread safety

    async def _get_lock(self, provider: str) -> asyncio.Lock:
        """Get or create a lock for a provider."""
        if provider not in self._locks:
            self._locks[provider] = asyncio.Lock()
        return self._locks[provider]

    async def record_failure(self, provider: str) -> None:
        """
        Record a failure for a provider.

        Args:
            provider: Provider name
        """
        lock = await self._get_lock(provider)
        async with lock:
            async with aiosqlite.connect(self.db_path) as db:
                now = datetime.utcnow().isoformat() + "Z"

                # Get or create circuit breaker entry
                cursor = await db.execute(
                    "SELECT * FROM circuit_breaker_state WHERE provider = ?",
                    (provider,)
                )
                row = await cursor.fetchone()

                if not row:
                    # Create new entry
                    await db.execute(
                        """
                        INSERT INTO circuit_breaker_state
                        (provider, state, failure_count, last_failure_at, last_state_change_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (provider, CircuitState.CLOSED.value, 1, now, now, now, now)
                    )
                else:
                    failure_count = row[2] + 1

                    # Check if failures exceed threshold within window
                    if failure_count >= self.FAILURE_THRESHOLD:
                        # Get count of failures in the last 5 minutes
                        cutoff = (datetime.utcnow() - timedelta(minutes=self.FAILURE_WINDOW_MINUTES)).isoformat() + "Z"
                        cursor = await db.execute(
                            """
                            SELECT COUNT(*) FROM circuit_breaker_state
                            WHERE provider = ? AND last_failure_at >= ?
                            """,
                            (provider, cutoff)
                        )
                        recent_failures = (await cursor.fetchone())[0]

                        if recent_failures >= self.FAILURE_THRESHOLD:
                            # Open the circuit
                            logger.warning(
                                f"Circuit breaker OPENING for {provider} after {recent_failures} failures in {self.FAILURE_WINDOW_MINUTES} minutes"
                            )
                            await db.execute(
                                """
                                UPDATE circuit_breaker_state
                                SET state = ?, failure_count = ?, last_failure_at = ?, opened_at = ?, last_state_change_at = ?, updated_at = ?
                                WHERE provider = ?
                                """,
                                (CircuitState.OPEN.value, failure_count, now, now, now, now, provider)
                            )
                        else:
                            # Still in CLOSED state, just increment counter
                            await db.execute(
                                """
                                UPDATE circuit_breaker_state
                                SET failure_count = ?, last_failure_at = ?, updated_at = ?
                                WHERE provider = ?
                                """,
                                (failure_count, now, now, provider)
                            )
                    else:
                        # Increment failure count
                        await db.execute(
                            """
                            UPDATE circuit_breaker_state
                            SET failure_count = ?, last_failure_at = ?, updated_at = ?
                            WHERE provider = ?
                            """,
                            (failure_count, now, now, provider)
                        )

                await db.commit()

    async def record_success(self, provider: str) -> None:
        """
        Record a success for a provider.

        Args:
            provider: Provider name
        """
        lock = await self._get_lock(provider)
        async with lock:
            async with aiosqlite.connect(self.db_path) as db:
                now = datetime.utcnow().isoformat() + "Z"

                cursor = await db.execute(
                    "SELECT state FROM circuit_breaker_state WHERE provider = ?",
                    (provider,)
                )
                row = await cursor.fetchone()

                if row:
                    current_state = row[0]
                    if current_state == CircuitState.HALF_OPEN.value:
                        # Successful probe in HALF_OPEN -> close circuit
                        logger.info(f"Circuit breaker CLOSING for {provider} after successful probe")
                        await db.execute(
                            """
                            UPDATE circuit_breaker_state
                            SET state = ?, failure_count = 0, last_state_change_at = ?, updated_at = ?
                            WHERE provider = ?
                            """,
                            (CircuitState.CLOSED.value, now, now, provider)
                        )
                    elif current_state == CircuitState.CLOSED.value:
                        # Success in CLOSED state, reset failure count
                        await db.execute(
                            """
                            UPDATE circuit_breaker_state
                            SET failure_count = 0, updated_at = ?
                            WHERE provider = ?
                            """,
                            (now, provider)
                        )

                await db.commit()

    async def get_state(self, provider: str) -> CircuitState:
        """
        Get current state of a provider's circuit.

        Args:
            provider: Provider name

        Returns:
            Current CircuitState
        """
        lock = await self._get_lock(provider)
        async with lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT state, opened_at FROM circuit_breaker_state WHERE provider = ?",
                    (provider,)
                )
                row = await cursor.fetchone()

                if not row:
                    return CircuitState.CLOSED

                state_str = row[0]
                opened_at_str = row[1]

                # Check if we should transition from OPEN to HALF_OPEN
                if state_str == CircuitState.OPEN.value and opened_at_str:
                    opened_at = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                    now = datetime.utcnow()
                    cooldown_passed = (now - opened_at) > timedelta(minutes=self.COOLDOWN_MINUTES)

                    if cooldown_passed:
                        logger.info(f"Circuit breaker entering HALF_OPEN state for {provider} after {self.COOLDOWN_MINUTES} minutes")
                        now_str = datetime.utcnow().isoformat() + "Z"
                        await db.execute(
                            """
                            UPDATE circuit_breaker_state
                            SET state = ?, last_state_change_at = ?, updated_at = ?
                            WHERE provider = ?
                            """,
                            (CircuitState.HALF_OPEN.value, now_str, now_str, provider)
                        )
                        await db.commit()
                        return CircuitState.HALF_OPEN

                return CircuitState(state_str)

    async def check_call_allowed(self, provider: str) -> bool:
        """
        Check if a call is allowed for a provider.

        Args:
            provider: Provider name

        Returns:
            True if call is allowed, False if circuit is OPEN

        Raises:
            CircuitBreakerError: If circuit is OPEN
        """
        state = await self.get_state(provider)

        if state == CircuitState.OPEN:
            raise CircuitBreakerError(f"Circuit breaker OPEN for {provider}")

        return True

    async def reset(self, provider: str) -> None:
        """
        Manually reset circuit breaker for a provider.

        Args:
            provider: Provider name
        """
        lock = await self._get_lock(provider)
        async with lock:
            async with aiosqlite.connect(self.db_path) as db:
                now = datetime.utcnow().isoformat() + "Z"

                logger.info(f"Manually resetting circuit breaker for {provider}")

                await db.execute(
                    """
                    UPDATE circuit_breaker_state
                    SET state = ?, failure_count = 0, last_state_change_at = ?, updated_at = ?
                    WHERE provider = ?
                    """,
                    (CircuitState.CLOSED.value, now, now, provider)
                )

                await db.commit()

    async def get_all_states(self) -> dict:
        """
        Get state of all circuit breakers.

        Returns:
            Dictionary mapping provider names to CircuitState values
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT provider, state FROM circuit_breaker_state")
            rows = await cursor.fetchall()

            return {row[0]: row[1] for row in rows}
