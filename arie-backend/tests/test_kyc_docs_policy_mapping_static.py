import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


def _backoffice_html() -> str:
    return BACKOFFICE_HTML.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    start = src.index(f"function {name}")
    brace = src.index("{", start)
    depth = 0
    for index in range(brace, len(src)):
        char = src[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[start : index + 1]
    raise AssertionError(f"could not extract function {name}")


def _run_node(script: str) -> None:
    assert shutil.which("node"), "Node.js is required for Back Office mapping checks"
    result = subprocess.run(
        ["node", "-"],
        input=script,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_policy_payload_mapping_accepts_canonical_fields_and_aliases():
    html = _backoffice_html()
    functions = "\n\n".join(
        _extract_function(html, name)
        for name in [
            "applyAgent1PolicyPayload",
            "normalizePolicyDocTypeKey",
            "pushPolicyLookupCandidate",
            "policyDocumentTypeCandidates",
            "isPersonScopedPolicyLookup",
            "isProofOfAddressLookupCandidate",
            "documentPolicyLookupCandidates",
            "agent1PolicyForDocument",
            "documentPrimaryIssue",
        ]
    )
    script = f"""
var AGENT1_DOCUMENT_POLICIES = [];
var AGENT1_WORKFLOW_USAGES = [];
var AGENT1_POLICY_SUMMARY = {{}};
var window = {{ AGENT1_DOCUMENT_POLICIES: AGENT1_DOCUMENT_POLICIES }};
var KYC_VERIFICATION_POLICY_MISSING_MESSAGE = 'Verification policy missing. Admin setup is required before automated verification can run. Manual review is required before relying on this document.';
var KYC_DOCUMENT_SLOT_LABELS = {{
  cert_inc: 'Certificate of Incorporation',
  memarts: 'Memorandum & Articles',
  reg_sh: 'Register of Shareholders',
  reg_dir: 'Register of Directors',
  poa: 'Proof of Registered Address',
  passport: 'Passport / Government ID',
  cert_reg: 'Certificate of Registration'
}};
function normalizeDetailValue(value) {{ return String(value || '').trim() || '—'; }}
function kycDocumentSlotLabel(doc, linkedRequirement) {{
  if (linkedRequirement && (linkedRequirement.requirement_label || linkedRequirement.requirement_key)) {{
    return linkedRequirement.requirement_label || linkedRequirement.requirement_key;
  }}
  var type = String((doc && doc.doc_type) || '').toLowerCase();
  return KYC_DOCUMENT_SLOT_LABELS[type] || normalizeDetailValue(type || 'Document');
}}
function enhancedRequirementBackOfficeGroup(req) {{
  return req && req.subject_scope === 'identity' ? 'identity' : 'enhanced';
}}
{functions}
function assert(condition, message) {{
  if (!condition) throw new Error(message);
}}
function policyIdFor(doc, expectedSlot, linkedRequirement) {{
  var policy = agent1PolicyForDocument(doc, linkedRequirement || null, expectedSlot || null);
  return policy && policy.policyId;
}}
applyAgent1PolicyPayload({{
  document_policies: [
    {{
      document_type: 'cert_inc',
      canonical_key: 'cert_inc',
      aliases: ['certificate_of_incorporation', 'coi'],
      policyId: 'DOC-ENTITY-COI-v1',
      active_pilot_status: 'Active',
      backend_executable: true,
      material_checks: [{{ label: 'Entity Name Match' }}],
      technical_checks: []
    }},
    {{
      document_type: 'memarts',
      aliases: ['memorandum_and_articles', 'articles_of_association'],
      policy_id: 'DOC-ENTITY-MEMARTS-v1',
      active_pilot_status: 'Active',
      backend_executable: true,
      material_checks: [{{ label: 'Completeness' }}],
      technical_checks: []
    }},
    {{
      document_type: 'reg_sh',
      aliases: ['register_of_shareholders', 'shareholder_register'],
      policyId: 'DOC-ENTITY-REGSH-v1',
      active_pilot_status: 'Active',
      backend_executable: true,
      material_checks: [],
      technical_checks: []
    }},
    {{
      document_type: 'poa',
      aliases: ['proof_of_registered_address', 'registered_address_proof'],
      policyId: 'DOC-ENTITY-REGISTERED-ADDRESS-v1',
      active_pilot_status: 'Active',
      backend_executable: true,
      material_checks: [],
      technical_checks: []
    }},
    {{
      document_type: 'poa_person',
      aliases: ['personal_poa', 'residential_address_proof'],
      policyId: 'DOC-PERSON-ADDRESS-v1',
      active_pilot_status: 'Active',
      backend_executable: true,
      material_checks: [],
      technical_checks: []
    }},
    {{
      document_type: 'cert_reg',
      aliases: ['business_registration'],
      policyId: 'DOC-ENTITY-REGISTRATION-v1',
      active_pilot_status: 'Manual review only',
      backend_executable: false,
      material_checks: [],
      technical_checks: []
    }}
  ]
}});
var certPolicy = window.AGENT1_DOCUMENT_POLICIES.find(function(policy) {{ return policy.policyId === 'DOC-ENTITY-COI-v1'; }});
assert(certPolicy.docTypes.indexOf('cert_inc') >= 0, 'document_type should be available as docTypes candidate');
assert(certPolicy.docTypes.indexOf('certificate_of_incorporation') >= 0, 'aliases should be available as docTypes candidates');
assert(policyIdFor({{ doc_type: 'cert_inc' }}) === 'DOC-ENTITY-COI-v1', 'cert_inc should map from document_type');
assert(policyIdFor({{ doc_type: 'Certificate-Of-Incorporation' }}) === 'DOC-ENTITY-COI-v1', 'certificate alias should match case/hyphen variants');
assert(policyIdFor({{ doc_type: 'memorandum and articles' }}) === 'DOC-ENTITY-MEMARTS-v1', 'memarts alias should match space variant');
assert(policyIdFor({{ slot_key: 'entity:memarts' }}) === 'DOC-ENTITY-MEMARTS-v1', 'canonical slot key tail should match when doc_type is unavailable');
assert(policyIdFor({{ doc_type: 'register-of-shareholders' }}) === 'DOC-ENTITY-REGSH-v1', 'shareholder register alias should match hyphen variant');
assert(policyIdFor({{ doc_type: 'poa' }}, {{ doc_type: 'poa', label: 'Proof of Registered Address', person_id: null }}) === 'DOC-ENTITY-REGISTERED-ADDRESS-v1', 'entity poa should map to registered-address policy');
assert(policyIdFor({{ doc_type: 'poa', person_id: 'director-1' }}, {{ doc_type: 'poa', label: 'Proof of Address', person_id: 'director-1' }}) === 'DOC-PERSON-ADDRESS-v1', 'person poa should map to person address policy');
assert(policyIdFor({{ doc_type: 'cert_reg' }}) === 'DOC-ENTITY-REGISTRATION-v1', 'manual-review-only policies should still be recognised');
assert(policyIdFor({{ doc_type: 'unmapped_standard_doc' }}) === null, 'unmapped documents should not silently match');
assert(documentPrimaryIssue({{ doc_type: 'unmapped_standard_doc', verification_results: {{}} }}, {{ label: 'Failed' }}, null, {{ doc_type: 'unmapped_standard_doc', label: 'Unmapped document' }}) === KYC_VERIFICATION_POLICY_MISSING_MESSAGE, 'true missing policy warning should remain');
"""
    _run_node(script)


def test_backoffice_policy_mapping_uses_canonical_policy_fields_without_changing_copy():
    html = _backoffice_html()
    assert "normalized.docTypes = policyDocumentTypeCandidates(normalized);" in html
    assert "pushPolicyLookupCandidate(candidates, policy.document_type);" in html
    assert "pushPolicyLookupCandidate(candidates, policy.canonical_key);" in html
    assert "policy.aliases.forEach(function(candidate)" in html
    assert "function documentPolicyLookupCandidates(doc, linkedRequirement, expectedSlot)" in html
    assert "agent1PolicyForDocument(doc, linkedRequirement, expectedSlot)" in html
    assert "poa_person" in html
    assert (
        "Verification policy missing. Admin setup is required before automated verification can run. "
        "Manual review is required before relying on this document."
    ) in html
    assert "System setup issue: verification policy missing." not in html
