#!/usr/bin/env python3
"""
ARIE Finance Resilience Layer
==============================

Production-grade external API resilience patterns for the ARIE onboarding platform.

Includes:
- Retry policy with exponential backoff
- Circuit breaker pattern
- Persistent task queue for failed operations
- Provider health tracking
- Resilient client wrappers for external APIs
- Workflow enforcement rules
- Comprehensive monitoring endpoints

Usage:
    from resilience import ResilientAPIClient, WorkflowEnforcer

    client = ResilientAPIClient(db_path="./arie.db")
    result = await client.call(
        provider="sumsub",
        endpoint="/kyc",
        func=some_async_func,
        application_id="app123",
        task_type="kyc_verification"
    )
"""

from .retry_policy import RetryPolicy, RetryPolicyError
from .circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerError
from .task_queue import ExternalTaskQueue, QueueTask, TaskStatus
from .provider_tracker import ProviderStatusTracker
from .resilient_client import ResilientAPIClient
from .integration_wrappers import (
    ResilientSumsubClient,
    ResilientOpenSanctionsClient,
    ResilientOpenCorporatesClient,
    ResilientClaudeClient,
)
from .workflow_rules import WorkflowEnforcer
from .db_schema import init_resilience_tables, init_resilience_tables_sync
from .monitoring import (
    ResilienceMetricsHandler,
    ResilienceQueueHandler,
    RetryQueueTaskHandler,
    CircuitBreakerResetHandler,
    ResilienceHealthHandler,
    ResilienceStatusHandler,
    get_resilience_routes,
)

__version__ = "1.0.0"
__all__ = [
    # Core classes
    "RetryPolicy",
    "CircuitBreaker",
    "ExternalTaskQueue",
    "ProviderStatusTracker",
    "ResilientAPIClient",
    # Enums
    "CircuitState",
    "TaskStatus",
    # Exceptions
    "RetryPolicyError",
    "CircuitBreakerError",
    # Data classes
    "QueueTask",
    # Integration wrappers
    "ResilientSumsubClient",
    "ResilientOpenSanctionsClient",
    "ResilientOpenCorporatesClient",
    "ResilientClaudeClient",
    # Workflow
    "WorkflowEnforcer",
    # Database
    "init_resilience_tables",
    "init_resilience_tables_sync",
    # Monitoring
    "get_resilience_routes",
    "ResilienceMetricsHandler",
    "ResilienceQueueHandler",
    "RetryQueueTaskHandler",
    "CircuitBreakerResetHandler",
    "ResilienceHealthHandler",
    "ResilienceStatusHandler",
]
