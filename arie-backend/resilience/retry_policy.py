#!/usr/bin/env python3
"""
Retry policy with exponential backoff and jitter for resilient API calls.
"""

import asyncio
import logging
import random
import time
from typing import Any, Callable, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class RetryPolicyError(Exception):
    """Raised when all retry attempts have been exhausted."""
    pass


class RetryPolicy:
    """
    Implements exponential backoff with jitter for resilient API calls.

    Configuration:
    - Max retries: 3
    - Base delay: 1 second
    - Max delay: 32 seconds
    - Jitter: random 0-25% of delay
    - Retry on: timeout, connection error, HTTP 429, 500, 502, 503, 504
    """

    MAX_RETRIES = 3
    BASE_DELAY_SECONDS = 1
    MAX_DELAY_SECONDS = 32
    JITTER_FACTOR = 0.25

    # HTTP status codes that trigger a retry
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    # Exception types that trigger a retry
    RETRYABLE_EXCEPTIONS = (
        asyncio.TimeoutError,
        ConnectionError,
        TimeoutError,
        OSError,
    )

    def __init__(self, max_retries: int = MAX_RETRIES):
        """
        Initialize retry policy.

        Args:
            max_retries: Maximum number of retry attempts (default: 3)
        """
        self.max_retries = max_retries

    async def execute(
        self,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Execute a function with retry policy.

        Args:
            func: Async function to execute
            *args: Positional arguments to pass to func
            **kwargs: Keyword arguments to pass to func

        Returns:
            Result of successful function execution

        Raises:
            RetryPolicyError: If all retries are exhausted
        """
        last_exception = None
        last_status_code = None

        for attempt in range(self.max_retries + 1):
            try:
                result = await func(*args, **kwargs)

                # Check if result is a response object with status_code
                if hasattr(result, "status_code"):
                    status_code = result.status_code
                    if status_code in self.RETRYABLE_STATUS_CODES:
                        if attempt < self.max_retries:
                            last_status_code = status_code
                            delay = self._calculate_backoff(attempt)
                            logger.warning(
                                f"Retryable HTTP status {status_code} on attempt {attempt + 1}/{self.max_retries + 1}, "
                                f"backing off {delay:.2f}s"
                            )
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise RetryPolicyError(
                                f"HTTP {status_code} after {self.max_retries} retries"
                            )
                    else:
                        # Non-retryable status code, return result
                        logger.info(f"Successfully completed call on attempt {attempt + 1}")
                        return result
                else:
                    # Successful execution (no status code)
                    logger.info(f"Successfully completed call on attempt {attempt + 1}")
                    return result

            except self.RETRYABLE_EXCEPTIONS as e:
                last_exception = e

                if attempt < self.max_retries:
                    delay = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Retryable exception {type(e).__name__}: {e} on attempt {attempt + 1}/{self.max_retries + 1}, "
                        f"backing off {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"Failed after {self.max_retries} retries: {type(e).__name__}: {e}"
                    )
                    raise RetryPolicyError(
                        f"{type(e).__name__} after {self.max_retries} retries: {e}"
                    ) from e

            except Exception as e:
                # Non-retryable exception, fail immediately
                logger.error(f"Non-retryable exception: {type(e).__name__}: {e}")
                raise

        # Should not reach here, but just in case
        if last_exception:
            raise RetryPolicyError(
                f"Unexpected failure after {self.max_retries} retries"
            ) from last_exception
        else:
            raise RetryPolicyError(
                f"Failed with HTTP {last_status_code} after {self.max_retries} retries"
            )

    def _calculate_backoff(self, attempt: int) -> float:
        """
        Calculate exponential backoff with jitter.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            Delay in seconds
        """
        # Exponential backoff: 1, 2, 4, 8, 16, 32...
        delay = min(self.BASE_DELAY_SECONDS * (2 ** attempt), self.MAX_DELAY_SECONDS)

        # Add jitter: random 0-25% of delay
        jitter = random.uniform(0, delay * self.JITTER_FACTOR)
        total_delay = delay + jitter

        return total_delay
