"""Manual step: grant Gemini Enterprise agents IAP egress access to their Data Agents.

Run this only after adding one or more Data Agents to a Gemini Enterprise app.
It is deliberately not part of the CI/CD pipeline.

    python -m deployer.ge_egress --project_id=PROJECT [--dry-run] [--agent_ids ID [ID ...]]

By default it targets every agent defined in agent_cards/ and low_code_agents/.
Pass --agent_ids to limit it to specific Data Agent IDs.

The caller needs roles/discoveryengine.viewer (to find GE agents) and permission
to update IAM on the project and iap_web (e.g. roles/iam.securityAdmin).
"""

from __future__ import annotations

import argparse
import sys

import httpx

from deployer import iam, specs
from deployer.auth import auth_headers, get_credentials
from deployer.cli import load_all


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m deployer.ge_egress",
        description="Grant Gemini Enterprise agents the IAP egress roles needed to call their Data Agents.",
    )
    parser.add_argument("--project_id", required=True, help="Target GCP project ID")
    parser.add_argument("--location", default="global", help="Data Agent location (default: global)")
    parser.add_argument("--no_code_dir", default="agent_cards", help="Folder of A2A agent card JSON files")
    parser.add_argument("--low_code_dir", default="low_code_agents", help="Folder of low-code agent modules")
    parser.add_argument(
        "--agent_ids",
        nargs="+",
        default=None,
        help="Only these Data Agent IDs (default: all agents defined in the repo)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report missing bindings without changing IAM")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.agent_ids:
        agent_ids = set(args.agent_ids)
    else:
        try:
            agent_ids = {
                s.agent_id
                for s in load_all(args.no_code_dir, args.low_code_dir, args.project_id, args.location)
            }
        except specs.SpecError as e:
            print(f"Invalid agent definition: {e}", file=sys.stderr)
            sys.exit(2)

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"== Gemini Enterprise egress IAM ({mode}) project={args.project_id}")
    print(f"== Data Agent IDs: {sorted(agent_ids)}\n")

    credentials = get_credentials()
    with httpx.Client(headers=auth_headers(credentials, args.project_id), timeout=60.0) as http:
        try:
            iam.configure_egress(http, args.project_id, agent_ids, dry_run=args.dry_run)
        except httpx.HTTPStatusError as e:
            print(
                f"[ERROR] {e}\n    response: {e.response.text.strip()[:500]}",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.dry_run:
        print("\n[dry run: nothing was changed]")


if __name__ == "__main__":
    main()
