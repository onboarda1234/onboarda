# Applications Module Scope Verification

Application Review changes:
- AI Compliance Supervisor tab remains visible as Enterprise / Coming Soon.
- The tab no longer renders active AI Governance, Explainability, Case Aggregate, Agent Results, Contradictions, Compliance Rules Triggered, or Run Analysis controls.
- Legacy `runSupervisorPipeline()` remains guarded by `isAiSupervisorPilotActive()` before any `/supervisor/run` request.
- Legacy `loadSupervisorForApp()` exits when AI Supervisor is inactive before any `/supervisor/result` request.

Monitoring alert changes:
- Active `File SAR` action removed from modal.
- SAR/STR display is disabled and labelled `SAR/STR Coming Soon`.
- Legacy `triggerSARFromAlert()` exits when SAR/STR flags are inactive before any `/sar/auto-trigger` request.

Active pilot workflows intentionally left in place:
- Applications list and detail shell.
- KYC document review / Agent 1.
- Screening queue and review.
- Risk scoring display and exports.
- Enhanced Requirements.
- Monitoring alert review/escalation/dismissal except SAR/STR filing.
- Periodic Review.
- Normal audit trail.
- Reports.

