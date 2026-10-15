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

"""Export a published Data Agent into a declarative JSON config in agent_configs/.

    python -m deployer.export_agent \\
        --source projects/PROJECT/locations/LOCATION/dataAgents/AGENT_ID \\
        --out agent_configs/my_agent.json [--agent_id my_agent]

The config is built from the Data Agent resource (DataAgentService.GetDataAgent),
which is the source of truth for the agent's published context. A2A agent cards
no longer carry tables, instructions or example queries, so they can't be used to
recreate an agent.

--source also accepts any URL containing projects/.../locations/.../dataAgents/...,
such as the agent card "url".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from google.api_core import exceptions
from google.cloud import geminidataanalytics

from deployer import specs
from deployer.auth import AuthError, get_credentials
from deployer.endpoints import data_agent_client

RESOURCE_RE = re.compile(r"projects/([^/]+)/locations/([^/]+)/dataAgents/([^/?#]+)")


class ExportError(ValueError):
    """Raised when a Data Agent can't be exported into a deployable config."""


def parse_source(source: str) -> tuple[str, str, str]:
    match = RESOURCE_RE.search(source)
    if not match:
        raise ValueError(
            f"--source '{source}' must contain projects/<p>/locations/<l>/dataAgents/<id>."
        )
    return match.group(1), match.group(2), match.group(3)


def default_agent_id(display_name: str, fallback: str) -> str:
    """Turn a display name such as "My Sales Agent" into a valid ID ("my_sales_agent")."""
    slug = re.sub(r"[^a-z0-9_-]+", "_", display_name.lower()).strip("_-")
    if slug and not slug[0].isalpha():
        slug = f"agent_{slug}"
    slug = slug[:63].rstrip("_-")
    return slug if specs.AGENT_ID_RE.fullmatch(slug) else fallback


def agent_to_config(
    agent: geminidataanalytics.DataAgent,
    agent_id: str,
    source_project_id: str,
    location: str | None = None,
) -> dict:
    """Convert a live DataAgent into the agent_configs/ JSON schema.

    Only the published context is exported, and server-populated fields (table
    schemas, timestamps, the staging context) are dropped. Settings the schema
    doesn't cover, such as table and column descriptions or schema relationships,
    are not exported.
    """
    ctx = agent.data_analytics_agent.published_context
    if not ctx.datasource_references.bq.table_references:
        raise ExportError(
            f"Data Agent '{agent.name}' has no published BigQuery table references. "
            "Publish the agent in BigQuery Studio first."
        )

    if location is None:
        try:
            _, location, _ = parse_source(agent.name)
        except ValueError:
            location = "global"
    location = location.lower()

    return {
        "agent_id": agent_id,
        "location": location,
        "display_name": agent.display_name,
        "description": agent.description,
        "labels": dict(sorted(agent.labels.items())),
        "source_project_id": source_project_id,
        "system_instruction": ctx.system_instruction,
        "table_references": [
            {"project_id": t.project_id, "dataset_id": t.dataset_id, "table_id": t.table_id}
            for t in ctx.datasource_references.bq.table_references
        ],
        "example_queries": [
            {
                "natural_language_question": q.natural_language_question,
                "sql_query": q.sql_query,
                "parameters": [
                    {"name": p.name, "description": p.description, "data_type": p.data_type}
                    for p in q.parameters
                ],
            }
            for q in ctx.example_queries
        ],
        "glossary_terms": [
            {"display_name": g.display_name, "description": g.description, "labels": list(g.labels)}
            for g in ctx.glossary_terms
        ],
        "options": {
            "python_enabled": ctx.options.analysis.python.enabled,
            "model": ctx.options.model.name,
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m deployer.export_agent",
        description="Save a published Data Agent as a declarative JSON config.",
    )
    parser.add_argument("--source", required=True, help="Data Agent resource name or URL")
    parser.add_argument("--out", required=True, help="Output path, e.g. agent_configs/my_agent.json")
    parser.add_argument(
        "--agent_id",
        help="Data Agent ID to deploy as (default: derived from the agent's display name)",
    )
    args = parser.parse_args(argv)

    try:
        project, location, source_agent_id = parse_source(args.source)
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    location = location.lower()

    try:
        client = data_agent_client(get_credentials(), location)
        agent = client.get_data_agent(
            name=specs.agent_resource_name(project, location, source_agent_id)
        )
    except AuthError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except exceptions.GoogleAPICallError as e:
        print(f"Fetching Data Agent failed: {e}", file=sys.stderr)
        sys.exit(1)

    agent_id = args.agent_id or default_agent_id(agent.display_name, source_agent_id)
    try:
        config = agent_to_config(agent, agent_id, project, location=location)
        # Validate with the same rules the deployer uses before writing anything.
        specs.parse_agent_config(config, args.out, project, location)
    except (ExportError, specs.SpecError) as e:
        print(f"Exported agent is not deployable: {e}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {args.out}")
    print(f"  deploys as:      {agent_id} (location: {config['location']})")
    print(f"  tables:          {len(config['table_references'])}")
    print(f"  example queries: {len(config['example_queries'])}")
    print(f"  glossary terms:  {len(config['glossary_terms'])}")
    print(f"  instructions:    {len(config['system_instruction'])} chars")


if __name__ == "__main__":
    main()
