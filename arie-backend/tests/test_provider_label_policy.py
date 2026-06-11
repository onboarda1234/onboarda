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
