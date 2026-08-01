# Compliance Memo validation report

Validation date: 2026-08-01

Source: GitHub `onboarda1234/onboarda`, `main` at `c667f95ff8ae892bcc8cafe27efa1151cc7d92f6`
Implementation branch: `codex/compliance-memo-workspace`

## Validation environment

- GitHub-truth implementation branch fast-forwarded to current `origin/main` before the authoritative release gate.
- Full suite on a remediated local host with 10.16 GiB available and a fresh SSL-enabled PostgreSQL 16 database supplied through `TEST_POSTGRES_DSN`.
- Google Chrome viewport: 1440 × 1100. The actual back-office HTML and current sample PDF bytes were served by an ephemeral local validation harness so the native PDF viewer was painted into the evidence.
- PDF renderer: WeasyPrint 69.0; page inspection: Poppler.
- The installed in-app browser-control surface was unavailable in this task's tool set, so the browser skill's approved fallback was implemented with the bundled Playwright runtime and local Google Chrome.
- The release-candidate browser rerun reported zero page or console errors. No production data was used.

The demo fixture's real Generate Memo attempt correctly failed its existing document-evidence gate with HTTP 409. To exercise visual states without weakening or bypassing that gate in product code, deterministic memo rows were inserted only into the disposable validation database. Approval behavior is covered by the unchanged handler tests and the focused regression suite.

## Functional checks

- NOT_GENERATED exposes only Generate Memo as the primary action.
- DRAFT shows the canonical generated PDF with a visible `DRAFT - NOT APPROVED` presentation.
- AWAITING_OFFICER_SIGNOFF is projected from existing validation and consistency controls.
- Officer rationale is saved through `PATCH` on the existing memo resource and stored in existing `review_notes`/`reviewed_by` columns.
- FINAL is read-only and exposes Download Final PDF.
- SUPERSEDED appears only on non-current history rows.
- Timestamp-derived staleness overrides draft/final state, blocks preview/approval, and requires regeneration.
- Preview and download call the same PDF generator. A fresh authenticated post-review handler check returned `200` for both, used `inline` versus `attachment` disposition only, reported lifecycle `FINAL`, and produced byte-identical 44,499-byte PDFs with SHA-256 `c95b37a8793f54edf58abccecba51edfe20ad82a3bae153b79659cee5630025a`.
- The iframe displays a Blob URL created from the authenticated PDF response; no second HTML memo is generated.
- Superseded PDF requests are aborted, and a response is discarded if its captured application/memo identity no longer matches the active workspace.
- The existing final-memo rationale lock is enforced atomically in the rationale update; the approval handler remains unchanged.

## Screenshot index

| # | Evidence | File |
|---|---|---|
| 1 | Overview memo workspace before generation | [01-not-generated.png](01-not-generated.png) |
| 2 | Draft memo generated | [02-draft-generated.png](02-draft-generated.png) |
| 3 | Actual generated PDF embedded in the workspace | [03-pdf-preview.png](03-pdf-preview.png) |
| 4 | Saved officer rationale | [04-officer-rationale.png](04-officer-rationale.png) |
| 5 | Final approved and locked memo | [05-final-locked.png](05-final-locked.png) |
| 6 | Current v2 plus superseded v1 history | [06-version-history.png](06-version-history.png) |
| 7 | Stale memo with regeneration requirement | [07-stale-state.png](07-stale-state.png) |
| 8 | Complete workspace at 1440px desktop width | [08-desktop-responsive.png](08-desktop-responsive.png) |

Every screenshot captures the complete memo workspace rather than a cropped sub-panel.

## PDF validation

| Artefact | SHA-256 | Pages | Format |
|---|---|---:|---|
| [compliance-memo-draft-sample.pdf](compliance-memo-draft-sample.pdf) | `1ac94cad698f2485dde34c6b10c906c07ff0c4338eab749d47a59859904505e4` | 3 | A4, PDF 1.7 |
| [compliance-memo-final-sample.pdf](compliance-memo-final-sample.pdf) | `3720b008f766d493621b0fe855d34c3fb63879a5d1b3c7dfe5584d761173cd18` | 3 | A4, PDF 1.7 |

Checks performed:

- all nine required headings, including Memo Basis, are present in extracted text;
- draft and final hashes differ;
- draft pages have a visible diagonal watermark and red draft status;
- final pages have a green approved/locked status and no draft watermark;
- page breaks are clean;
- no text or table overlaps were observed;
- tables stay within the page box;
- headers, footers, and page numbers are consistent;
- timestamps are compact and do not overflow document-control cells.
- the title and running header consistently use `RegMind Compliance Memorandum`;
- recommendation and authoritative risk data use typographic hierarchy rather than warning-style badges;
- conditions are numbered and every page carries the professional governance footer.

Rendered page evidence:

- Draft: [page 1](draft-page-1.png), [page 2](draft-page-2.png), [page 3](draft-page-3.png)
- Final: [page 1](final-page-1.png), [page 2](final-page-2.png), [page 3](final-page-3.png)

Before/after PDF screenshots:

- Draft before: [page 1](before-draft-page-1.png), [page 2](before-draft-page-2.png), [page 3](before-draft-page-3.png)
- Draft after: [page 1](after-draft-page-1.png), [page 2](after-draft-page-2.png), [page 3](after-draft-page-3.png)
- Final before: [page 1](before-final-page-1.png), [page 2](before-final-page-2.png), [page 3](before-final-page-3.png)
- Final after: [page 1](after-final-page-1.png), [page 2](after-final-page-2.png), [page 3](after-final-page-3.png)
- Exact wording changes are recorded in [polish-report.md](polish-report.md).
- The production-quality presentation pass and its immediate before/after evidence are recorded in [final-polish-report.md](final-polish-report.md).

## Automated validation

- Authoritative post-review repository-wide gate: **8,910 passed, 11 skipped, 4 expected failures, 0 failed, 0 errors** in 19m 05s.
- PostgreSQL-only tests ran successfully against the fresh `TEST_POSTGRES_DSN`; they were not omitted or downgraded to partial evidence.
- Focused memo/UI/API/governance/approval coverage and final PDF wording/layout coverage are included in that complete gate.
- Live post-review memo PDF handler check: preview/download both `200`, exact byte equality, `FINAL` lifecycle, and matching SHA-256 `c95b37a8793f54edf58abccecba51edfe20ad82a3bae153b79659cee5630025a`.
- Python compilation: `server.py`, `pdf_generator.py`, and `base_handler.py` passed.
- Browser JavaScript parse: all 3 inline back-office scripts passed Node `vm.Script` parsing.
- `git diff --check`: passed.

## Human-review notes

- Complete staging UAT through the unchanged real approval gates remains part of the post-deployment release validation.
- Security should confirm the narrow CSP addition `frame-src 'self' blob:` is acceptable for authenticated PDF embedding.
- The existing PDF endpoint still renders on demand rather than storing immutable PDF bytes; the existing SHA/audit headers remain the integrity mechanism.
