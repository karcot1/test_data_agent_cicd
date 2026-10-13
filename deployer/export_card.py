"""Export a published Data Agent's full A2A card into agent_cards/.

    python -m deployer.export_card \\
        --source projects/PROJECT/locations/global/dataAgents/AGENT_ID \\
        --out agent_cards/my_agent.json

Use this instead of copying the A2A JSON from the BigQuery UI. The UI copy omits
the 'BQ Dataset Information' extension (tables, system instructions, example
queries), which the deployer needs. The authenticated API card includes it.

--source also accepts the card's full "url" value (anything containing
projects/.../locations/.../dataAgents/...).
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import httpx

from deployer import specs
from deployer.auth import auth_headers, get_credentials

GDA_A2A_API = "https://geminidataanalytics.googleapis.com/v1/a2a"
RESOURCE_RE = re.compile(r"projects/([^/]+)/locations/([^/]+)/dataAgents/([^/?#]+)")


def parse_source(source: str) -> tuple[str, str, str]:
    match = RESOURCE_RE.search(source)
    if not match:
        raise ValueError(
            f"--source '{source}' must contain projects/<p>/locations/<l>/dataAgents/<id>."
        )
    return match.group(1), match.group(2), match.group(3)


def fetch_card(client: httpx.Client, project: str, location: str, agent_id: str) -> dict:
    resp = client.get(
        f"{GDA_A2A_API}/projects/{project}/locations/{location}/dataAgents/{agent_id}/v1/card"
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Fetching card failed ({resp.status_code}): {resp.text.strip()[:500]}")
    return resp.json()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m deployer.export_card",
        description="Save a Data Agent's full A2A card (with BQ Dataset Information) to a file.",
    )
    parser.add_argument("--source", required=True, help="Data Agent resource name or card URL")
    parser.add_argument("--out", required=True, help="Output path, e.g. agent_cards/my_agent.json")
    args = parser.parse_args(argv)

    try:
        project, location, agent_id = parse_source(args.source)
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)

    credentials = get_credentials()
    with httpx.Client(headers=auth_headers(credentials, project), timeout=60.0) as client:
        try:
            card = fetch_card(client, project, location, agent_id)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            sys.exit(1)

    # Validate with the same rules the deployer uses before writing anything.
    try:
        spec = specs.parse_agent_card(card, args.out, project, location)
    except specs.SpecError as e:
        print(f"Exported card is not deployable: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(card, f, indent=2, ensure_ascii=False)
        f.write("\n")

    ctx = spec.data_agent.data_analytics_agent.published_context
    print(f"Wrote {args.out}")
    print(f"  deploys as:      {spec.agent_id}")
    print(f"  tables:          {len(ctx.datasource_references.bq.table_references)}")
    print(f"  example queries: {len(ctx.example_queries)}")
    print(f"  instructions:    {len(ctx.system_instruction)} chars")


if __name__ == "__main__":
    main()
