# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Manual step: grant Gemini Enterprise agents IAP egress access to their Data Agents.

Run this only after adding one or more Data Agents to a Gemini Enterprise app.
It is deliberately not part of the CI/CD pipeline.

    python -m deployer.ge_egress --project_id=PROJECT [--dry-run] [--agent_ids ID [ID ...]]

By default it targets every agent defined in agent_configs/ and low_code_agents/.
Pass --agent_ids to limit it to specific Data Agent IDs.

The caller needs roles/discoveryengine.viewer (to find GE agents) and permission
to update IAM on the project and iap_web (e.g. roles/iam.securityAdmin).
"""

from __future__ import annotations

import argparse
import sys

import httpx

from deployer import iam, specs
from deployer.auth import AuthError, auth_headers, get_credentials
from deployer.cli import load_all


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m deployer.ge_egress",
        description="Grant Gemini Enterprise agents the IAP egress roles needed to call their Data Agents.",
    )
    parser.add_argument("--project_id", required=True, help="Target GCP project ID")
    parser.add_argument(
        "--location", default="global", type=str.lower, help="Data Agent location (default: global)"
    )
    parser.add_argument("--config_dir", default="agent_configs", help="Folder of no-code agent JSON configs")
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
                for s in load_all(args.config_dir, args.low_code_dir, args.project_id, args.location)
            }
        except specs.SpecError as e:
            print(f"Invalid agent definition: {e}", file=sys.stderr)
            sys.exit(2)

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"== Gemini Enterprise egress IAM ({mode}) project={args.project_id}")
    print(f"== Data Agent IDs: {sorted(agent_ids)}\n")

    try:
        credentials = get_credentials()
    except AuthError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    with httpx.Client(headers=auth_headers(credentials, args.project_id), timeout=60.0) as http:
        try:
            iam.configure_egress(http, args.project_id, agent_ids, dry_run=args.dry_run)
        except httpx.HTTPStatusError as e:
            print(
                f"[ERROR] {e}\n    response: {e.response.text.strip()[:500]}",
                file=sys.stderr,
            )
            sys.exit(1)
        except RuntimeError as e:  # setIamPolicy failures
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)

    if args.dry_run:
        print("\n[dry run: nothing was changed]")


if __name__ == "__main__":
    main()
