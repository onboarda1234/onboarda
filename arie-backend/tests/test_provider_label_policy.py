from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()


def _iter_policy_files():
    excluded_dirs = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        "venv",
        ".venv",
        "tmp",
    }
    excluded_prefixes = {
        REPO_ROOT / "docs" / "audits",
    }
    excluded_files = {
        SELF,
        REPO_ROOT / "arie-treasury-portal.html",
    }
    allowed_suffixes = {
        ".html",
        ".jsx",
        ".js",
        ".py",
        ".md",
        ".txt",
        ".sh",
        ".yaml",
        ".yml",
    }

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path in excluded_files:
            continue
        if any(part in excluded_dirs for part in path.parts):
            continue
        if any(path.is_relative_to(prefix) for prefix in excluded_prefixes):
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        yield path


def _scan_for(phrases):
    matches = []
    for path in _iter_policy_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lowered = text.lower()
        for phrase in phrases:
            if phrase in lowered:
                matches.append(f"{path.relative_to(REPO_ROOT)}: {phrase}")
    return matches


def test_no_removed_provider_references_in_product_surfaces():
    variants = [
        "open" + "sanctions",
        "open" + "sanction",
        "open " + "sanctions",
        "open-" + "sanctions",
        "open_" + "sanctions",
    ]
    assert _scan_for(variants) == []


def test_backoffice_html_has_no_removed_provider_runtime_variants():
    html = (REPO_ROOT / "arie-backoffice.html").read_text(encoding="utf-8").lower()
    variants = [
        "open" + "sanctions",
        "open" + "sanction",
        "open " + "sanctions",
        "open-" + "sanctions",
        "open_" + "sanctions",
    ]
    assert [variant for variant in variants if variant in html] == []


def test_sumsub_labels_are_identity_verification_only():
    prohibited = [
        "sumsub " + "aml",
        "sumsub " + "sanctions",
        "sumsub " + "watchlist",
        "sumsub " + "pep",
        "sumsub " + "adverse media",
        "sumsub " + "screening",
        "sumsub " + "customer screening",
        "sumsub " + "company screening",
        "sumsub " + "monitoring",
    ]
    assert _scan_for(prohibited) == []


def test_complyadvantage_is_not_labelled_identity_verification():
    prohibited = [
        "complyadvantage " + "identity",
        "complyadvantage " + "kyc",
        "ca " + "identity verification",
    ]
    assert _scan_for(prohibited) == []


def test_screening_queue_filter_is_source_not_runtime_provider_selector():
    html = (REPO_ROOT / "arie-backoffice.html").read_text()
    assert ">Source" in html
    assert "Provider/source" not in html
    assert "ComplyAdvantage Mesh screening source" in html
    assert "Sumsub IDV/KYC source" in html
    assert "OpenCorporates registry source" in html


def test_provider_status_panel_uses_backend_runtime_truth():
    html = (REPO_ROOT / "arie-backoffice.html").read_text()
    assert "screening-provider-status-panel" in html
    assert "Active AML Screening Provider" in html
    assert "ComplyAdvantage Mesh only when backend active=true" in html
    assert "CA Mesh Active" in html
    assert "Identity Verification Provider" in html
    assert "IDV Status (Sumsub)" in html
    assert "AML Entitlement (Sumsub)" not in html
    assert "Screening Abstraction" in html
    assert "loadScreeningProviderStatus" in html
    assert "Configured screening provider" not in html


def test_unknown_provider_does_not_render_as_complyadvantage_mesh():
    html = (REPO_ROOT / "arie-backoffice.html").read_text()

    assert "Unknown Provider" in html
    assert "|| 'ComplyAdvantage'" not in html
    assert "|| 'ComplyAdvantage Mesh'" not in html
