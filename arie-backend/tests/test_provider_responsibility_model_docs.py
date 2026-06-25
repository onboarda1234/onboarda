from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "architecture" / "sumsub-complyadvantage-provider-model.md"


def test_provider_responsibility_architecture_doc_exists():
    assert DOC_PATH.exists()


def test_provider_responsibility_doc_contains_required_authority_terms():
    text = DOC_PATH.read_text(encoding="utf-8")

    required_terms = [
        "Sumsub",
        "ComplyAdvantage Mesh",
        "IDV / identity verification",
        "Liveness / face match",
        "Identity document checks",
        "Sanctions screening",
        "PEP screening",
        "Watchlists",
        "Adverse media",
        "Material screening concern",
        "IDV approval gate",
        "Screening/adverse-media approval gate",
        "Legacy And Non-Authoritative Fields",
        "Source article link not available from ComplyAdvantage Mesh payload.",
    ]

    missing = [term for term in required_terms if term not in text]
    assert missing == []
