#!/usr/bin/env python3
"""
RegMind — Console Code-Impact Scanner
======================================
Terminal utility that reads `latest-impact.json` (running generate-impact-map.py 
first if needed) and prints a high-fidelity console report detailing changed files,
downstream compliance risk levels, and the active validation checklist.
"""

import os
import sys
import json
import subprocess


# ANSI color escapes
CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_RED = "\033[31m"
CLR_GREEN = "\033[32m"
CLR_YELLOW = "\033[33m"
CLR_BLUE = "\033[34m"
CLR_MAGENTA = "\033[35m"
CLR_CYAN = "\033[36m"


def get_color(risk):
    risk_lower = str(risk).lower()
    if "stable" in risk_lower or "low" in risk_lower or "green" in risk_lower:
        return CLR_GREEN
    if "changed" in risk_lower or "blue" in risk_lower:
        return CLR_BLUE
    if "review" in risk_lower or "medium" in risk_lower or "yellow" in risk_lower or "amber" in risk_lower:
        return CLR_YELLOW
    if "critical" in risk_lower or "red" in risk_lower or "high" in risk_lower:
        return CLR_RED
    return CLR_RESET


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dashboard_dir = os.path.dirname(script_dir)
    latest_impact_path = os.path.join(dashboard_dir, "data", "latest-impact.json")

    # Run generator script first to ensure we display live metrics
    generator_path = os.path.join(script_dir, "generate-impact-map.py")
    try:
        subprocess.run(
            [sys.executable, generator_path],
            check=True,
            capture_output=True,
            text=True
        )
    except Exception as exc:
        print(f"Warning: Could not execute pre-scan generation: {exc}", file=sys.stderr)

    if not os.path.exists(latest_impact_path):
        print(f"Error: Impact data file not found at '{latest_impact_path}'. Run generate-impact-map.py first.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(latest_impact_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as exc:
        print(f"Error reading impact JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 80)
    print(f" {CLR_BOLD}REGMIND WORKFLOW CODE-IMPACT SCANNER REPORT{CLR_RESET}")
    print("=" * 80)

    # 1. Commit / Branch summary
    print(f" {CLR_BOLD}Branch:{CLR_RESET} {data.get('branch', 'unknown')}")
    print(f" {CLR_BOLD}Commit SHA:{CLR_RESET} {data.get('commit_sha', 'unknown')}")
    print(f" {CLR_BOLD}Generated At:{CLR_RESET} {data.get('generated_at', 'unknown')}")

    risk = data.get("risk_level", "STABLE")
    color = get_color(risk)
    print(f" {CLR_BOLD}Overall Risk Rating: {CLR_RESET}{color}{CLR_BOLD}{risk}{CLR_RESET}")
    print("-" * 80)

    # 2. Changed Files
    changed_files = data.get("changed_files", [])
    print(f" {CLR_BOLD}Changed Files ({len(changed_files)}):{CLR_RESET}")
    if not changed_files:
        print(f"  {CLR_GREEN}No changed files detected. Repository is stable.{CLR_RESET}")
    else:
        for f in changed_files:
            f_color = get_color(f.get("risk_level", "low"))
            f_path = f.get("file_path", "unknown")
            f_area = f.get("area", "unmapped").upper()
            f_risk = f.get("risk_level", "low").upper()
            print(f"  • {CLR_BOLD}{f_path}{CLR_RESET}")
            print(f"    {CLR_CYAN}[{f_area}]{CLR_RESET} Risk: {f_color}{f_risk}{CLR_RESET} | {f.get('plain_english_impact', '')}")

    print("-" * 80)

    # 3. Affected Workflows & Agents
    affected_wfs = data.get("affected_workflows", [])
    affected_ags = data.get("affected_agents", [])

    print(f" {CLR_BOLD}Affected Compliance Workflows ({len(affected_wfs)}):{CLR_RESET}")
    if not affected_wfs:
        print("  None")
    else:
        print("  " + ", ".join(f"{CLR_YELLOW}{wf}{CLR_RESET}" for wf in affected_wfs))

    print(f"\n {CLR_BOLD}Affected AI Agents ({len(affected_ags)}):{CLR_RESET}")
    if not affected_ags:
        print("  None")
    else:
        print("  " + ", ".join(f"{CLR_MAGENTA}{ag}{CLR_RESET}" for ag in affected_ags))

    print("-" * 80)

    # 4. Required Validation Checklist
    validation = data.get("validation_required", [])
    print(f" {CLR_BOLD}REQUIRED VALIDATION CHECKLIST ({len(validation)}):{CLR_RESET}")
    if not validation:
        print(f"  {CLR_GREEN}✓ No verification required for stable codebase.{CLR_RESET}")
    else:
        for idx, val in enumerate(validation, 1):
            print(f"  [{CLR_RED}!{CLR_RESET}] {idx}. {val}")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
