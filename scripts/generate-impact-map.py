#!/usr/bin/env python3
"""
RegMind — Git Commit Code-Impact Map Generator
===============================================
Parses the git diff between a base branch/commit and a head commit, 
cross-references changed files against the compliance code-map database,
calculates overall risk severity, compiles a validation checklist, and
writes a static latest-impact.json file to drive the HTML dashboard.
"""

import os
import sys
import json
import subprocess
import argparse
from datetime import datetime, timezone


def get_git_output(cmd, cwd=None):
    """Executes a git command and returns clean string output, returning empty on errors."""
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=True
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as err:
        print(f"Git command failed: {cmd}\nError: {err.stderr.strip()}", file=sys.stderr)
        return ""
    except Exception as exc:
        print(f"Error running git: {exc}", file=sys.stderr)
        return ""


def load_json_file(path):
    """Reads a JSON file safely, returning empty structure on errors."""
    if not os.path.exists(path):
        print(f"Warning: File not found at '{path}'", file=sys.stderr)
        return []
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as exc:
        print(f"Error reading JSON from '{path}': {exc}", file=sys.stderr)
        return []


def write_json_file(path, data):
    """Writes data as prettified JSON, making parent directories if needed."""
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, sort_keys=True, ensure_ascii=False)
        print(f"Successfully generated impact map at '{path}'")
        return True
    except Exception as exc:
        print(f"Error writing JSON to '{path}': {exc}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Generate RegMind Workflow Code-Impact JSON")
    parser.add_argument("--base", default="origin/main", help="Base branch/commit to diff against")
    parser.add_argument("--head", default="HEAD", help="Head branch/commit (default: HEAD)")
    parser.add_argument("--output", default=None, help="Path to write the latest-impact.json file")
    args = parser.parse_args()

    # Determine paths relative to repo root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dashboard_dir = os.path.dirname(script_dir)
    repo_root = os.path.dirname(dashboard_dir)

    output_path = args.output
    if not output_path:
        output_path = os.path.join(dashboard_dir, "data", "latest-impact.json")

    code_map_path = os.path.join(dashboard_dir, "data", "code-map.json")
    workflow_map_path = os.path.join(dashboard_dir, "data", "workflow-map.json")

    # Load schemas
    code_map = load_json_file(code_map_path)
    workflow_map = load_json_file(workflow_map_path)

    # 1. Fetch active git context
    commit_sha = get_git_output("git rev-parse HEAD", cwd=repo_root) or "unknown"
    branch = get_git_output("git rev-parse --abbrev-ref HEAD", cwd=repo_root) or "unknown"

    # 2. Get list of changed files
    # Check if base commit is available in history, fall back to comparing against HEAD~1 if not
    git_verify = get_git_output(f"git cat-file -t {args.base}", cwd=repo_root)
    if not git_verify:
        print(f"Warning: Base revision '{args.base}' is invalid or not in history. Falling back to 'HEAD~1' for local diff.")
        diff_cmd = f"git diff --name-only HEAD~1 {args.head}"
    else:
        diff_cmd = f"git diff --name-only {args.base} {args.head}"

    changed_raw = get_git_output(diff_cmd, cwd=repo_root)
    changed_files = [line.strip() for line in changed_raw.split("\n") if line.strip()]

    # If working tree is not clean, also check uncommitted / untracked changes
    status_raw = get_git_output("git status --porcelain", cwd=repo_root)
    for line in status_raw.split("\n"):
        if line.strip():
            parts = line.strip().split(" ", 1)
            filepath = parts[1].strip() if len(parts) > 1 else ""
            # If untracked file, it might be renamed; handle basic extraction
            if " -> " in filepath:
                filepath = filepath.split(" -> ")[1].strip()
            if filepath and filepath not in changed_files:
                changed_files.append(filepath)

    print(f"Scanning {len(changed_files)} changed files between {args.base} and {args.head}...")

    # 3. Intersect changes with code map
    mapped_changes = {item["file_path"]: item for item in code_map}

    affected_workflows = set()
    affected_agents = set()
    validation_required = set()
    impacted_file_details = []

    highest_risk = "stable"
    risk_rank = {"stable": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

    for path in changed_files:
        # Standardize path matching to match database entries
        standardized_path = path.replace("\\", "/")
        
        # Check if the file is registered in our compliance map
        if standardized_path in mapped_changes:
            meta = mapped_changes[standardized_path]
            
            # Aggregate downstreams
            for wf in meta.get("affected_workflows", []):
                affected_workflows.add(wf)
            for ag in meta.get("affected_agents", []):
                affected_agents.add(ag)
            for val in meta.get("validation_required", []):
                validation_required.add(val)
                
            file_risk = meta.get("risk_level", "low").lower()
            if risk_rank.get(file_risk, 1) > risk_rank.get(highest_risk, 0):
                highest_risk = file_risk

            impacted_file_details.append({
                "file_path": path,
                "area": meta.get("area", "unknown"),
                "risk_level": file_risk,
                "affected_workflows": meta.get("affected_workflows", []),
                "affected_agents": meta.get("affected_agents", []),
                "plain_english_impact": meta.get("plain_english_impact", "No documented impact.")
            })
        else:
            # Unmapped utility or configuration file — assign generic low impact
            impacted_file_details.append({
                "file_path": path,
                "area": "unmapped",
                "risk_level": "low",
                "affected_workflows": [],
                "affected_agents": [],
                "plain_english_impact": "Unmapped documentation or scratch script file. Low risk."
            })
            if risk_rank["low"] > risk_rank.get(highest_risk, 0):
                highest_risk = "low"

    # Enforce workflow-importance promotion:
    # If any critical compliance workflow is affected, elevate the risk minimum to "high"
    critical_workflows = {wf["id"] for wf in workflow_map if wf.get("compliance_importance") == "critical"}
    affected_critical_wfs = affected_workflows.intersection(critical_workflows)
    if affected_critical_wfs and risk_rank.get(highest_risk, 0) < risk_rank["high"]:
        print(f"Risk elevated to HIGH because critical workflows are affected: {list(affected_critical_wfs)}")
        highest_risk = "high"

    # Create the output model
    impact_data = {
        "commit_sha": commit_sha,
        "branch": branch,
        "changed_files": impacted_file_details,
        "affected_workflows": sorted(list(affected_workflows)),
        "affected_agents": sorted(list(affected_agents)),
        "risk_level": highest_risk.upper() if highest_risk != "stable" else "STABLE",
        "validation_required": sorted(list(validation_required)),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    # Write the compiled result
    write_json_file(output_path, impact_data)


if __name__ == "__main__":
    main()
