# Claude AI Integration Module for ARIE Finance

## Overview

The `claude_client.py` module provides AI-powered compliance assessment for the ARIE Finance platform, powering:

- **Risk Scoring Engine**: Scores applications across 5 compliance dimensions
- **Agent 2**: External DB Cross-Verification (client data vs registry)
- **Agent 4**: Corporate Structure & UBO Mapping (ownership analysis)
- **Agent 5**: Compliance Memo Generation (final narrative)

## Quick Start

### Installation

```bash
# Install Anthropic library
pip install anthropic

# Set API key
export ANTHROPIC_API_KEY=sk-...
```

### Basic Usage

```python
from claude_client import ClaudeClient

# Initialize client
client = ClaudeClient(
    api_key="sk-...",  # or use ANTHROPIC_API_KEY env var
    monthly_budget_usd=50.0
)

# Score risk
risk = client.score_risk(application_data)
print(f"Risk Level: {risk['risk_level']}, Score: {risk['overall_score']}")

# Cross-verify data
verification = client.cross_verify_data(client_data, registry_data)
print(f"Match: {verification['overall_match']}")

# Analyze structure
structure = client.analyze_corporate_structure(directors, ubos, jurisdiction)
print(f"Complexity: {structure['complexity_score']}/10")

# Generate memo
memo = client.generate_compliance_memo(application_data, agent_results)
print(f"Recommendation: {memo['approval_recommendation']}")

# Check budget
in_budget, message = client.check_budget()
print(f"Monthly spend: {message}")
```

## Configuration

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | - | Claude API key (required for live mode) |
| `CLAUDE_MOCK_MODE` | false | Use realistic mock responses (no API calls) |

### Mock Mode (Testing)

```python
# Enable mock mode (realistic responses, no API calls)
client = ClaudeClient(mock_mode=True)

# Useful for testing without spending tokens
result = client.score_risk(test_data)  # Returns realistic mock response
```

### Budget Management

```python
client = ClaudeClient(monthly_budget_usd=100.0)

# Check current spending
stats = client.get_usage_stats()
# {
#     "total_cost_usd": 12.45,
#     "monthly_budget_usd": 100.0,
#     "remaining_budget_usd": 87.55,
#     "total_api_calls": 23,
#     "usages": [...]
# }

# Check if under budget
in_budget, message = client.check_budget()
# (True, "$12.45 / $100.00")
```

## API Methods

### 1. score_risk(application_data)

Scores application risk across 5 compliance dimensions.

**Model**: claude-sonnet-4-6 (fast, cheap)

**Input**:
```python
{
    "company_name": "Example Corp Ltd",
    "entity_type": "Private Company",
    "country": "Mauritius",
    "sector": "Technology",
    "directors": [
        {"full_name": "John Smith", "nationality": "UK", "is_pep": "No"}
    ],
    "ubos": [
        {"full_name": "Jane Doe", "ownership_pct": 100, "is_pep": "No"}
    ],
    "ownership_structure": "Simple",
    # ... other fields
}
```

**Output**:
```python
{
    "overall_score": 58,
    "risk_level": "MEDIUM",
    "dimensions": {
        "jurisdiction_risk": {
            "score": 3,
            "factors": ["High FATF compliance"]
        },
        "entity_structure": {
            "score": 2,
            "factors": ["Standard corporate form"]
        },
        "beneficial_ownership": {
            "score": 3,
            "factors": ["Clear ownership chain"]
        },
        "financial_crime": {
            "score": 2,
            "factors": ["No PEP connections"]
        },
        "document_integrity": {
            "score": 2,
            "factors": ["Complete documentation"]
        }
    },
    "flags": ["flag1", "flag2"],
    "recommendation": "REVIEW"
}
```

### 2. cross_verify_data(client_data, registry_data)

Compares client-submitted data against official registry records (OpenCorporates, local registries, etc.).

**Model**: claude-sonnet-4-6 (fast, cheap)

**Input**:
```python
client_data = {
    "company_name": "Example Corp Ltd",
    "company_number": "12345678",
    "directors": ["John Smith", "Jane Doe"]
}

registry_data = {
    "company_name": "Example Corp Limited",
    "company_number": "12345678",
    "directors": ["John Smith", "Jane Doe"]
}
```

**Output**:
```python
{
    "overall_match": "PARTIAL",
    "checks": [
        {
            "field": "company_name",
            "submitted": "Example Corp Ltd",
            "registry": "Example Corp Limited",
            "match": True
        },
        {
            "field": "directors",
            "submitted": ["John Smith", "Jane Doe"],
            "registry": ["John Smith", "Jane Doe"],
            "match": True
        }
    ],
    "risk_flags": [
        "Minor name variation (Ltd vs Limited) — acceptable",
        "All official records match"
    ],
    "confidence": 0.95
}
```

### 3. analyze_corporate_structure(directors, ubos, jurisdiction)

Maps ownership layers and identifies beneficial ownership, nominee arrangements, and structural risks.

**Model**: claude-sonnet-4-6 (fast, cheap)

**Input**:
```python
directors = [
    {"name": "John Smith", "nationality": "UK", "company_owned": False}
]

ubos = [
    {"name": "Jane Doe", "ownership_pct": 100, "nationality": "Mauritius"}
]

jurisdiction = "Mauritius"
```

**Output**:
```python
{
    "structure_type": "Simple holding",
    "complexity_score": 2,
    "ubo_identified": True,
    "nominee_indicators": [],
    "jurisdiction_flags": [],
    "shell_company_risk": "LOW",
    "recommendations": [
        "Standard annual beneficial ownership update"
    ]
}
```

### 4. generate_compliance_memo(application_data, agent_results)

Generates a comprehensive, board-ready compliance assessment memo synthesizing all agent findings.

**Model**: claude-opus-4-6 (thorough, detailed)

**Input**:
```python
application_data = {...}  # Full application data

agent_results = {
    "risk_score": {...},  # From score_risk()
    "cross_verification": {...},  # From cross_verify_data()
    "structure_analysis": {...},  # From analyze_corporate_structure()
    "screening_results": {...}  # From external screening APIs
}
```

**Output**:
```python
{
    "memo_html": "<h2>Compliance Assessment Memo</h2>...",
    "summary": "Medium-risk entity with identifiable UBO...",
    "risk_rating": "MEDIUM",
    "key_findings": [
        "UBO chain successfully mapped",
        "No adverse media findings",
        "All required documents complete"
    ],
    "recommendations": [
        "Annual beneficial ownership update",
        "Quarterly transaction monitoring"
    ],
    "review_checklist": [
        "Company identity verified",
        "UBO chain mapped",
        "PEP screening completed",
        # ... 8 items total
    ],
    "approval_recommendation": "APPROVE_WITH_CONDITIONS"
}
```

## Model Selection

| Task | Model | Why |
|------|-------|-----|
| Risk Scoring | claude-sonnet-4-6 | Fast + cost-effective for structured scoring |
| Cross-Verification | claude-sonnet-4-6 | Fast pattern matching + comparison |
| Structure Analysis | claude-sonnet-4-6 | Good at analyzing complex hierarchies |
| Compliance Memo | claude-opus-4-6 | Superior narrative quality for final report |

**Pricing** (per 1M tokens, Feb 2025):
- Sonnet input: $3.00 | Sonnet output: $15.00
- Opus input: $15.00 | Opus output: $45.00

## Error Handling

The module includes built-in resilience:

```python
# Retry logic (max 3 attempts with exponential backoff)
# Timeout handling (30-second default timeout)
# Automatic fallback to mock responses when API unavailable
# Clear logging of failures
```

### Common Issues

**Issue**: "No API key provided — falling back to mock mode"
- **Solution**: Set `ANTHROPIC_API_KEY` environment variable or pass `api_key` parameter

**Issue**: Network/Connection errors
- **Solution**: Module auto-retries up to 3 times. Check logs for details.

**Issue**: Exceeding monthly budget
- **Solution**: Module logs warnings. Adjust `monthly_budget_usd` or implement spending controls

## Integration with server.py

The module is designed to integrate seamlessly with existing compliance endpoints:

```python
# In server.py or handlers

from claude_client import ClaudeClient

# Initialize once (in startup)
claude_client = ClaudeClient(monthly_budget_usd=50.0)

# Use in handlers
class ApplicationRiskHandler(BaseHandler):
    def post(self, app_id):
        # Fetch application data
        app_data = {...}
        
        # Score risk
        risk_result = claude_client.score_risk(app_data)
        
        # Store result
        db.execute("UPDATE applications SET risk_score=?, risk_level=?",
                   (risk_result['overall_score'], risk_result['risk_level']))
```

## Testing

Run the module's built-in tests:

```bash
python3 -m pytest tests/test_claude_client.py -v
```

Quick manual test:

```bash
python3 claude_client.py
```

Expected output: Mock responses demonstrating all 4 AI agents.

## Token Usage & Costs

Monitor token consumption:

```python
stats = client.get_usage_stats()

# Examine usage history
for usage in stats['usages']:
    print(f"{usage['model']}: {usage['input_tokens']} in, {usage['output_tokens']} out, ${usage['cost_usd']:.4f}")
```

## Feature Flags

```bash
# Disable real API calls (useful in dev/testing)
export CLAUDE_MOCK_MODE=true
python3 server.py

# Re-enable live API
export CLAUDE_MOCK_MODE=false
export ANTHROPIC_API_KEY=sk-...
python3 server.py
```

## References

- [Anthropic API Documentation](https://docs.anthropic.com/)
- [Claude Models](https://docs.anthropic.com/en/docs/about-claude/models/overview)
- [AML/CFT Risk Assessment Standards](https://www.fatf-gafi.org/)

---

**Module**: `/sessions/loving-awesome-bell/mnt/Onboarda/arie-backend/claude_client.py`  
**Version**: 1.0  
**Last Updated**: 2025-03-16
