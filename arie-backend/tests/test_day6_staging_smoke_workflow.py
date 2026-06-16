from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "day6-staging-smoke.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-staging.yml"


def _workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def test_day6_staging_smoke_workflow_is_manual_and_staging_scoped():
    text = _workflow_text()

    assert "workflow_dispatch:" in text
    assert "environment: staging" in text
    assert "concurrency:" in text
    assert "day6-staging-smoke" in text
    assert "permissions:" in text
    assert "contents: read" in text


def test_day6_staging_smoke_mints_masked_short_lived_token_in_actions():
    text = _workflow_text()

    assert "aws secretsmanager get-secret-value" in text
    assert "--secret-id \"$SECRET_ID\"" in text
    assert "JWT_SECRET" in text
    assert '"sub": "github-actions:day6-staging-smoke"' in text
    assert '"role": "sco"' in text
    assert '"iss": "arie-finance"' in text
    assert '"exp": now + 1800' in text
    assert "hmac.new(secret, signing_input.encode(\"ascii\"), hashlib.sha256)" in text
    assert "echo \"::add-mask::$SMOKE_TOKEN\"" in text
    assert "echo \"BACKOFFICE_TOKEN=$SMOKE_TOKEN\" >> \"$GITHUB_ENV\"" in text


def test_day6_staging_smoke_uses_token_env_not_literal_token_argument():
    text = _workflow_text()

    assert "arie-backend/scripts/qa/day5_closing_smoke.py" in text
    assert "--token-env BACKOFFICE_TOKEN" in text
    assert "Optional expected analytics total" in text
    assert "default: \"\"" in text
    assert 'if [ -n "${{ inputs.expected_total }}" ]; then' in text
    assert 'args+=(--expected-total "${{ inputs.expected_total }}")' in text
    assert 'if [ -n "${{ inputs.expected_pending }}" ]; then' in text
    assert 'args+=(--expected-pending "${{ inputs.expected_pending }}")' in text
    assert 'if [ -n "${{ inputs.expected_edd }}" ]; then' in text
    assert 'args+=(--expected-edd "${{ inputs.expected_edd }}")' in text
    assert "--token \"$SMOKE_TOKEN\"" not in text
    assert "--token $SMOKE_TOKEN" not in text


def test_deploy_staging_pins_runtime_version_environment():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    for expected in (
        "upsert_env('ENVIRONMENT', 'staging')",
        "upsert_env('SERVICE_NAME', 'regmind-backend')",
        "upsert_env('GIT_SHA', '$GIT_SHA')",
        "upsert_env('BUILD_TIME', '$BUILD_TIME')",
        "upsert_env('IMAGE_TAG', '$IMAGE_TAG')",
        'upsert_env("ENVIRONMENT", "staging")',
        'upsert_env("SERVICE_NAME", "regmind-verification-worker")',
        'upsert_env("GIT_SHA", os.environ["GIT_SHA"])',
        'upsert_env("BUILD_TIME", os.environ["BUILD_TIME"])',
        'upsert_env("IMAGE_TAG", os.environ["IMAGE_TAG"])',
    ):
        assert expected in text
