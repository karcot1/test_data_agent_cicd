"""Command-line entrypoint: python -m deployer --project_id=PROJECT [--dry-run].

Gemini Enterprise egress IAM is intentionally NOT part of this pipeline; run
`python -m deployer.ge_egress` manually after adding agents to a GE app.
"""

from __future__ import annotations

import argparse
import sys

import httpx
from google.cloud import geminidataanalytics

from deployer import specs
from deployer.auth import auth_headers, get_credentials
from deployer.reconcile import CREATE, ERROR, MODIFY, SKIP, Result, reconcile
from deployer.registry import AgentRegistry


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m deployer",
        description="Create/update only the Data Agents whose definitions changed "
        "and sync them to Agent Registry.",
    )
    parser.add_argument("--project_id", required=True, help="Target GCP project ID")
    parser.add_argument("--location", default="global", help="Data Agent location (default: global)")
    parser.add_argument("--no_code_dir", default="agent_cards", help="Folder of A2A agent card JSON files")
    parser.add_argument("--low_code_dir", default="low_code_agents", help="Folder of low-code agent modules")
    parser.add_argument("--dry-run", action="store_true", help="Report planned changes without applying them")
    parser.add_argument("--skip-registry", action="store_true", help="Skip Agent Registry sync")
    return parser.parse_args(argv)


def load_all(
    no_code_dir: str, low_code_dir: str, project_id: str, location: str
) -> list[specs.AgentSpec]:
    all_specs = specs.load_no_code_specs(no_code_dir, project_id, location)
    all_specs += specs.load_low_code_specs(low_code_dir, project_id, location)
    specs.validate(all_specs)
    return all_specs


def process_spec(spec, data_agent_client, registry, args) -> Result:
    result = reconcile(data_agent_client, spec, dry_run=args.dry_run)

    # Agent Registry only applies to no-code (A2A card) agents.
    if spec.card_data is None or registry is None:
        return result

    missing = registry.missing_locations(spec.agent_id)
    if result.action == SKIP and not missing:
        return result
    if result.action == SKIP:
        result.action = MODIFY
        result.changed_fields.append(f"agent_registry (missing in {missing})")

    if not args.dry_run:
        registry.sync(spec.agent_id, spec.card_data)
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
        all_specs = load_all(args.no_code_dir, args.low_code_dir, args.project_id, args.location)
    except specs.SpecError as e:
        print(f"Invalid agent definition: {e}", file=sys.stderr)
        sys.exit(2)

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"== Data Agent deployer ({mode}) project={args.project_id} location={args.location}")
    print(f"== {len(all_specs)} agent definition(s) found\n")
    if not all_specs:
        return

    credentials = get_credentials()
    data_agent_client = geminidataanalytics.DataAgentServiceClient(credentials=credentials)

    results: list[Result] = []
    with httpx.Client(headers=auth_headers(credentials, args.project_id), timeout=60.0) as http:
        registry = None if args.skip_registry else AgentRegistry(http, args.project_id, args.location)

        for spec in all_specs:
            try:
                result = process_spec(spec, data_agent_client, registry, args)
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
