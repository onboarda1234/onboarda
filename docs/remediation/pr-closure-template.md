# PR Closure Template

Use this template for every remediation PR closure report. Save the completed report as:

`docs/audits/evidence/remediation_sprints/<PR-ID>_<short-name>_<YYYYMMDDTHHMMSSZ>/closure_report.md`

## PR name

`PR-<N> - <name>`

## Linked remediation IDs

- `<ID>`

## Original issue summary

Briefly restate the issue from the tracker or audit.

## Re-diagnosis result

- Current `origin/main` SHA:
- Branch name:
- Branch commit SHA:
- Does the issue still exist on current `origin/main`?
- Evidence:

## Root cause

Describe the exact root cause. Avoid generic labels such as "bug" or "UI issue".

## Files changed

- `<path>`

## Behaviour before fix

Describe the reproduced behavior before the fix.

## Behaviour after fix

Describe the expected and verified behavior after the fix.

## Tests added/updated

- `<test file or scenario>`

## Targeted test results

Command:

```bash
<command>
```

Result:

```text
<result>
```

## Full suite results

Command:

```bash
<command>
```

Result:

```text
<result>
```

## Browser test results, if applicable

- Browser:
- URL:
- Role:
- Steps:
- Result:
- Screenshot path:

## Staging deploy evidence

- Merged main SHA:
- Deployment mechanism:
- ECS/task/image evidence, if applicable:
- Deployed at:

## /api/version evidence

Endpoint:

```text
<staging /api/version URL>
```

Result:

```json
{
  "git_sha": "<sha>",
  "image_tag": "<sha>"
}
```

Verdict:

- [ ] `git_sha` equals merged main SHA
- [ ] `image_tag` equals merged main SHA

## API smoke test evidence

- Endpoint(s):
- Role/token type:
- Expected:
- Actual:
- Raw evidence path:

## Browser smoke test evidence, if applicable

- URL:
- Role:
- Expected:
- Actual:
- Screenshot path:
- Console/network notes:

## Screenshots/evidence folder path

`docs/audits/evidence/remediation_sprints/<PR-ID>_<short-name>_<YYYYMMDDTHHMMSSZ>/`

## Remaining risks

- `<risk>`

## Items not closed by this PR

- `<ID or description>`

## Final closure verdict

Choose one:

- `CLOSED`
- `PARTIALLY FIXED`
- `OPEN`
- `BLOCKED / NEEDS EVIDENCE`
- `NOT APPLICABLE`

Rationale:

`<short rationale>`
