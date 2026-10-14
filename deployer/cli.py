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

"""Command-line entrypoint: python -m deployer --project_id=PROJECT [--dry-run].

Gemini Enterprise egress IAM is intentionally NOT part of this pipeline; run
`python -m deployer.ge_egress` manually after adding agents to a GE app.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from deployer import specs
from deployer.auth import auth_headers, get_credentials
from deployer.endpoints import data_agent_client
from deployer.reconcile import CREATE, ERROR, MODIFY, SKIP, Result, reconcile
from deployer.registry import DEFAULT_REGISTRY_LOCATIONS, AgentRegistry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m deployer",
        description="Create/update only the Data Agents whose definitions changed "
        "and sync them to Agent Registry.",
    )
    parser.add_argument("--project_id", required=True, help="Target GCP project ID")
    parser.add_argument("--location", default="global", help="Data Agent location (default: global)")
    parser.add_argument("--config_dir", default="agent_configs", help="Folder of no-code agent JSON configs")
    parser.add_argument("--low_code_dir", default="low_code_agents", help="Folder of low-code agent modules")
    parser.add_argument("--dry-run", action="store_true", help="Report planned changes without applying them")
    parser.add_argument("--skip-registry", action="store_true", help="Skip Agent Registry sync")
    parser.add_argument(
        "--registry_locations",
        nargs="+",
        default=list(DEFAULT_REGISTRY_LOCATIONS),
        help="Agent Registry locations to publish no-code agents to (default: global). "
        "Example: --registry_locations global us-central1",
    )
    return parser.parse_args(argv)


def load_all(
    config_dir: str, low_code_dir: str, project_id: str, location: str
) -> list[specs.AgentSpec]:
    all_specs = specs.load_agent_configs(config_dir, project_id, location)
    all_specs += specs.load_low_code_specs(low_code_dir, project_id, location)
    specs.validate(all_specs)
    return all_specs


def process_spec(spec, data_agent_client, registry, args) -> Result:
    result = reconcile(data_agent_client, spec, dry_run=args.dry_run)

    # Agent Registry only applies to no-code agents.
    if not spec.register or registry is None:
        return result

    missing = registry.missing_locations(spec.agent_id)
    if result.action == SKIP and not missing:
        return result
    if result.action == SKIP:
        result.action = MODIFY
        result.changed_fields.append(f"agent_registry (missing in {missing})")

    if not args.dry_run:
        registry.sync(spec.agent_id, spec.data_agent.description)
    return result


def print_result(result: Result, dry_run: bool) -> None:
    prefix = "[PLAN] " if dry_run and result.action in (CREATE, MODIFY) else ""
    line = f"{prefix}[{result.action}] {result.agent_id}  ({result.source})"
    if result.changed_fields:
        line += f"  changed: {', '.join(result.changed_fields)}"
    if result.error:
        line += f"\n    error: {result.error}"
    print(line, flush=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        all_specs = load_all(args.config_dir, args.low_code_dir, args.project_id, args.location)
    except specs.SpecError as e:
        print(f"Invalid agent definition: {e}", file=sys.stderr)
        sys.exit(2)

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"== Data Agent deployer ({mode}) project={args.project_id} location={args.location}")
    print(f"== {len(all_specs)} agent definition(s) found\n")
    if not all_specs:
        return

    credentials = get_credentials()
    client = data_agent_client(credentials, args.location)

    results: list[Result] = []
    with httpx.Client(headers=auth_headers(credentials, args.project_id), timeout=60.0) as http:
        registry = None if args.skip_registry else AgentRegistry(
            http, args.project_id, args.location, registry_locations=args.registry_locations
        )

        for spec in all_specs:
            try:
                result = process_spec(spec, client, registry, args)
            except Exception as e:  # keep going; report at the end
                result = Result(spec.agent_id, spec.source, ERROR, error=f"{type(e).__name__}: {e}")
            print_result(result, args.dry_run)
            results.append(result)

    counts = {a: sum(r.action == a for r in results) for a in (CREATE, MODIFY, SKIP, ERROR)}
    print(
        f"\n== Summary: {counts[CREATE]} created, {counts[MODIFY]} modified, "
        f"{counts[SKIP]} skipped, {counts[ERROR]} failed"
        + (" [dry run: nothing was changed]" if args.dry_run else "")
    )
    if counts[ERROR]:
        sys.exit(1)
