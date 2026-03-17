# ARIE Finance Resilience Layer — Integration Guide

## Overview

The resilience layer provides production-grade patterns for handling external API failures:

1. **Retry Policy**: Exponential backoff with jitter (max 3 retries)
2. **Circuit Breaker**: Prevents cascading failures (opens after 5 failures in 5 minutes)
3. **Task Queue**: Persistent retry queue for failed operations
4. **Provider Tracking**: Monitors health and latency of external services
5. **Workflow Enforcement**: Prevents approval when critical services are unavailable

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Application Code                                                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │  ResilientAPIClient                  │
        │  ├─ check circuit breaker            │
        │  ├─ execute with retry policy        │
        │  ├─ record metrics                   │
        │  └─ enqueue failures to task queue   │
        └──────────────────────────────────────┘
                           │
        ┌──────────┬──────────┬─────────┐
        ▼          ▼          ▼         ▼
    ┌─────────┐┌─────────┐┌───────┐┌──────────┐
    │ Retry   ││Circuit  ││Task   ││Provider  │
    │Policy   ││Breaker  ││Queue  ││Tracker   │
    └─────────┘└─────────┘└───────┘└──────────┘
        │          │          │         │
        └──────────┴──────────┴─────────┘
                   │
                   ▼
           SQLite Database
     ├─ circuit_breaker_state
     ├─ external_retry_queue
     └─ external_api_attempts
```

## Installation

1. Add `aiosqlite>=0.17.0` to requirements.txt (already done)
2. Ensure the resilience tables are created at startup:

```python
# In server.py startup:
from resilience import init_resilience_tables_sync

# Initialize resilience tables
try:
    init_resilience_tables_sync(DB_PATH)
    logger.info("✅ Resilience layer initialized")
except Exception as e:
    logger.error(f"Failed to initialize resilience layer: {e}")
    raise
```

## Usage Patterns

### 1. Basic Resilient API Call

```python
from resilience import ResilientAPIClient

# Initialize once
client = ResilientAPIClient(db_path="./arie.db")

# Make a call
result = await client.call(
    provider="sumsub",
    endpoint="/kyc/verify",
    func=async_function_to_call,
    application_id="app_123",
    task_type="kyc_verification",  # For re-queueing on failure
    method="POST",
    arg1=value1,
    kwarg1=value1
)

# Check result
if result["success"]:
    data = result["data"]
else:
    error = result["error"]
    queued = result.get("queued", False)  # True if added to retry queue
```

### 2. Using Integration Wrappers

```python
from resilience import ResilientSumsubClient, ResilientOpenSanctionsClient

# Initialize wrappers
sumsub = ResilientSumsubClient(
    db_path="./arie.db",
    sumsub_client=existing_sumsub_client
)

sanctions = ResilientOpenSanctionsClient(
    db_path="./arie.db",
    sanctions_client=existing_sanctions_client
)

# Use them
result = await sumsub.verify_kyc(application_id, applicant_data)
result = await sanctions.screen_entity(application_id, name, country)

# On failure, application status is automatically updated:
# - KYC failure: "pending_external_retry" (blocking)
# - Sanctions failure: "pending_manual_review" (non-blocking)
```

### 3. Enforcing Workflow Rules

```python
from resilience import WorkflowEnforcer

enforcer = WorkflowEnforcer(db_path="./arie.db")

# Check if application can be approved
can_approve, blockers = await enforcer.can_approve(app_id)

if not can_approve:
    print(f"Cannot approve: {blockers}")
    # Example blockers:
    # - "KYC verification pending external retry (status: pending_external_retry)"
    # - "Sanctions screening not completed (status: simulated)"
    # - "Compliance memo not generated"

# Get detailed blocker information
blockers = await enforcer.get_application_blockers(app_id)

# Route to manual review
await enforcer.route_to_manual_review(
    app_id,
    "Sanctions screening failed, requires manual review"
)
```

### 4. Processing the Retry Queue

```python
from resilience import ResilientAPIClient
import asyncio

client = ResilientAPIClient(db_path="./arie.db")

# Call periodically (e.g., every 30 seconds):
async def process_queue_loop():
    while True:
        results = await client.process_retry_queue()
        print(f"Processed {results['processed']} tasks, {results['failed']} failed")
        await asyncio.sleep(30)

# Or manually
results = await client.process_retry_queue()
```

### 5. Monitoring Metrics

```python
from resilience import ResilientAPIClient

client = ResilientAPIClient(db_path="./arie.db")

# Get comprehensive metrics
metrics = await client.get_metrics()

print(f"Circuit states: {metrics['circuit_breakers']}")
print(f"Provider statuses: {metrics['provider_statuses']}")
print(f"Queue stats: {metrics['queue_stats']}")
print(f"Health: {metrics['health']}")
```

## Integration with Tornado Server

### Add Resilience Routes

```python
# In server.py, in make_app():
from resilience import get_resilience_routes

def make_app():
    routes = [
        # ... existing routes ...
    ]

    # Add resilience monitoring endpoints
    routes.extend(get_resilience_routes())

    return tornado.web.Application(routes, ...)
```

### Available Endpoints

All endpoints return JSON responses:

#### GET /api/resilience/metrics
Comprehensive metrics for all providers and queues.

```json
{
  "circuit_breakers": {
    "sumsub": "CLOSED",
    "opencorporates": "OPEN"
  },
  "provider_statuses": {
    "sumsub": {
      "success_rate": 0.95,
      "avg_latency_ms": 234.5,
      "total_calls": 100
    }
  },
  "queue_stats": {
    "total": 5,
    "by_status": {"pending": 3, "processing": 2},
    "dead_letter_count": 0
  },
  "health": {...},
  "timestamp": "2025-03-17T10:00:00Z"
}
```

#### GET /api/resilience/queue?status=pending&provider=sumsub
Get queue contents with optional filtering.

#### POST /api/resilience/queue/{task_id}/retry
Manually trigger retry of a specific task.

#### POST /api/resilience/circuit/{provider}/reset
Manually reset circuit breaker for a provider.

#### GET /api/resilience/health
Overall health check of the resilience layer.

#### GET /api/resilience/status
Quick status check (lightweight).

## Application Status Updates

New application statuses added by the resilience layer:

- `pending_external_retry`: Waiting for external API retry (blocking for KYC)
- `pending_manual_review`: Requires manual officer review (blocking for approval)
- `blocked_external_dependency`: Blocked by external service failure
- `api_failure`: External API failed

## Database Schema

### circuit_breaker_state
```sql
CREATE TABLE circuit_breaker_state (
    id INTEGER PRIMARY KEY,
    provider TEXT UNIQUE NOT NULL,
    state TEXT NOT NULL,           -- CLOSED, OPEN, HALF_OPEN
    failure_count INTEGER DEFAULT 0,
    last_failure_at TEXT,
    opened_at TEXT,
    last_state_change_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### external_retry_queue
```sql
CREATE TABLE external_retry_queue (
    id INTEGER PRIMARY KEY,
    task_type TEXT NOT NULL,       -- kyc_verification, sanctions_check, etc.
    application_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    payload TEXT NOT NULL,          -- JSON
    attempt_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 5,
    next_retry_at TEXT NOT NULL,
    last_error TEXT,
    status TEXT NOT NULL,           -- pending, processing, completed, failed, dead
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### external_api_attempts
```sql
CREATE TABLE external_api_attempts (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    method TEXT NOT NULL,
    application_id TEXT,
    status_code INTEGER,
    latency_ms INTEGER,
    retry_count INTEGER DEFAULT 0,
    circuit_state TEXT,
    outcome TEXT NOT NULL,          -- success, failure, timeout, circuit_open
    error_message TEXT,
    created_at TEXT NOT NULL
);
```

## Error Handling

### Retry Policy Errors

Retries are automatic for:
- Timeout errors (`asyncio.TimeoutError`, `TimeoutError`)
- Connection errors (`ConnectionError`, `OSError`)
- HTTP 429 (Too Many Requests)
- HTTP 500, 502, 503, 504 (Server Errors)

All other errors fail immediately.

### Circuit Breaker

Opens after 5 failures within 5 minutes. When open:
- New calls immediately fail without executing
- Tasks are enqueued to retry queue
- After 2 minutes cooldown, enters HALF_OPEN state
- HALF_OPEN allows 1 probe request
- Successful probe closes circuit

### Task Queue

Failed tasks are automatically enqueued with exponential backoff:
- Attempt 0: retry after 5 minutes
- Attempt 1: retry after 10 minutes
- Attempt 2: retry after 20 minutes
- Attempt 3+: retry after up to 24 hours
- Max 5 attempts (configurable)
- Dead letter queue for exceeded tasks

## Testing

```python
import pytest
from resilience import ResilientAPIClient

@pytest.mark.asyncio
async def test_resilient_call_success():
    client = ResilientAPIClient(db_path=":memory:")

    async def mock_func():
        return {"status": "ok"}

    result = await client.call(
        provider="test",
        endpoint="/test",
        func=mock_func,
        application_id="test_app"
    )

    assert result["success"] is True
    assert result["data"]["status"] == "ok"

@pytest.mark.asyncio
async def test_circuit_breaker_opens():
    client = ResilientAPIClient(db_path=":memory:")

    # Simulate 5 failures
    for i in range(5):
        await client.circuit_breaker.record_failure("test_provider")

    # Next call should fail immediately
    with pytest.raises(CircuitBreakerError):
        await client.circuit_breaker.check_call_allowed("test_provider")
```

## Best Practices

1. **Always provide task_type for queueing**: Enables automatic re-queueing on failure
2. **Use application_id for tracing**: Helps track which applications are affected
3. **Monitor queue stats regularly**: Check for dead letter queue buildup
4. **Set appropriate max_retries**: 5 retries = up to 24 hours of retry window
5. **Log failures manually**: Resilience layer logs, but application context matters
6. **Test with circuit breaker open**: Ensure graceful degradation
7. **Configure alerting on health metrics**: Watch for degraded providers
8. **Review dead letter queue**: Investigate why tasks eventually failed

## Deployment Checklist

- [ ] `aiosqlite` added to requirements.txt
- [ ] Resilience tables initialized at startup
- [ ] Resilience routes added to Tornado app
- [ ] Integration wrappers configured with existing clients
- [ ] WorkflowEnforcer integrated into approval logic
- [ ] Task queue processor loop running
- [ ] Monitoring dashboard created
- [ ] Alerting configured on health metrics
- [ ] Dead letter queue reviewed daily
- [ ] Circuit breaker thresholds tuned for your providers

## Troubleshooting

### Circuit breaker stuck OPEN
```python
# Reset manually via API
curl -X POST http://localhost:8080/api/resilience/circuit/sumsub/reset

# Or programmatically
await enforcer.circuit_breaker.reset("sumsub")
```

### Task queue not processing
- Check if `process_retry_queue()` is being called
- Review error logs for details
- Check dead letter queue for exceeded tasks

### High latency metrics
- Check provider status endpoint
- Review external provider's status page
- Consider increasing timeout values

### Application stuck in pending_external_retry
- Verify external provider is available
- Manually retry task via `/api/resilience/queue/{id}/retry`
- Route to manual review if provider is permanently unavailable

## Performance Considerations

- **Circuit breaker state**: In-memory per provider (minimal overhead)
- **Retry policy**: Max 3 retries with exponential backoff (10-30 seconds per call)
- **Task queue**: Batches 100 ready tasks per processing cycle
- **Metrics tracking**: Indexes on provider, status, created_at (fast queries)
- **Database**: All operations are async (non-blocking)

## Security Notes

- Sensitive payloads stored in task queue (JSON-serialized)
- No PII encryption at resilience layer (encrypt at application level)
- Database should be protected like any production database
- Monitoring endpoints are public (protect with authentication in production)
