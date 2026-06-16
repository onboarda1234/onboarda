# Browser Smoke

## Local Pre-PR Browser Status

The local disposable demo server was started successfully at `http://localhost:10100`.

The in-app Browser plugin was selected for local browser smoke, but this session did not expose the required JavaScript browser-control tool after tool discovery. Because the required browser-control surface was unavailable, no in-app browser screenshot was captured locally.

Fallback local HTTP/static smoke:

- `GET /backoffice`: `200 text/html`
- Served back-office HTML contains:
  - `Document Verification Policies`
  - `Underlying Verification Check Configuration`
  - `Enhanced / EDD Documents`
  - aligned Agent 1 copy
- Served back-office HTML does not contain the removed visible registry dashboard strings:
  - `Agent 1 Evidence Control Layer`
  - `Document Policy Registry`
  - `Canonical Policy Coverage`
- `GET /portal`: `200 text/html`
- Portal HTML still loads and the portal upload UI source remains present.

## Staging Browser Smoke

Pending until:

1. PR is merged.
2. Merged main is deployed to staging.
3. `/api/version` confirms `git_sha` and `image_tag` match the merge SHA.
4. Browser smoke is run against staging.

Required staging checks:

- Open Document Verification Policies.
- Confirm top architecture/registry dashboard has been removed.
- Confirm Underlying Verification Check Configuration remains.
- Confirm page is simpler and less technical.
- Open AI Agents -> Agent 1.
- Confirm Agent 1 wording/counts align with settings.
- Open Application Review and confirm A/B/C/D/E/F/G sections remain.
- Confirm section C Enhanced Evidence Documents remains available.
- Confirm uploaded documents still show View/Download.
- Open portal and confirm portal loads and upload UI is unchanged.
- If safe credentials/fixtures exist, test an enhanced document upload path.
- Confirm no console/network/server errors.
