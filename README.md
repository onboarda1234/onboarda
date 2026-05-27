# RegMind Workflow & Code Impact Map Dashboard

This self-contained dashboard provides a premium, interactive, and compliance-tech credible visual mapping of the end-to-end **RegMind Compliance Operating System**.

It enables compliance officers, developers, non-technical stakeholders, auditors, and investors to:
1. Trace the **15 operational stages** of the compliance onboarding-to-monitoring workflow.
2. Understand the exact **10 AI agents**, their roles, and authority classifications.
3. Track **code-impact metrics** in real-time by automatically mapping git commit file changes to affected compliance steps.
4. View a compiled, dynamically calculated **verification checklist** detailing exactly what pytests and smoke checks are required to de-risk any deployment.

---

## Directory Structure

```text
/regmind-flow-dashboard
  index.html                     # Premium HTML5 dashboard template
  assets/
    styles.css                   # Custom responsive dark-mode styles
    app.js                       # Frontend state & controller JS
  data/
    workflow-map.json            # Database detailing all 15 steps
    code-map.json                # Code index mapping modules to steps
    agents-map.json              # AI Agent definitions and parameters
    latest-impact.json           # Live JSON artifact populated by git
  scripts/
    generate-impact-map.py       # Git diff analysis & JSON generator
    scan-changed-files.py        # Lightweight developer terminal scanner
  README.md                      # This user manual
```

---

## Getting Started

### 1. Launching the Interactive Dashboard
To browse the visual timeline and agent mapping, open the folder using a local web server (needed for loading local JSON files dynamically):

```bash
# Navigate to the dashboard directory
cd regmind-flow-dashboard

# Start a lightweight Python web server
python3 -m http.server 8000
```
Then, open your web browser and navigate to:  
👉 **`http://localhost:8000`**

---

### 2. Scanning Code Changes Locally
Developers can analyze their uncommitted code changes or evaluate PR branches directly in the terminal before committing:

```bash
# Print a beautiful color-coded code-impact report in the terminal:
python3 regmind-flow-dashboard/scripts/scan-changed-files.py
```
This script automatically executes `generate-impact-map.py` to write your active git status into `data/latest-impact.json`, refreshing the local browser dashboard.

---

### 3. Generating the Impact Map Manually
You can manually parse the code impact between any two git commits or branches:

```bash
# Compare local HEAD against main branch:
python3 regmind-flow-dashboard/scripts/generate-impact-map.py --base origin/main --head HEAD

# Compare arbitrary revisions and output to a custom path:
python3 regmind-flow-dashboard/scripts/generate-impact-map.py --base b35c7b9 --head HEAD --output data/latest-impact.json
```

---

## Secure CI/CD Integration
To support continuous compliance automation, the dashboard is designed to integrate securely with GitHub Actions. It executes git analysis during builds, uploading a secure, unauthenticated static JSON artifact rather than calling credentials from the client-side browser:

A proposed CI/CD workflow is located in:  
📁 **`.github/workflows/update-flow-impact.yml`**

---

## Defensibility & Safety
* **Zero Core Intrusion:** No Tornado backend handlers, database schemas, or operational workflows are modified by this dashboard. It acts strictly as a branch-isolated, non-runtime visualization tool.
* **No Authentication Tokens:** Preserves client secrecy. The browser loads static JSON maps locally without making authenticated GitHub API queries.
