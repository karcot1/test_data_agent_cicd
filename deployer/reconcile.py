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

"""Compare desired agent specs with live Data Agents and create/update only what changed."""

from __future__ import annotations

from dataclasses import dataclass, field

from google.api_core import exceptions
from google.cloud import geminidataanalytics
from google.protobuf import field_mask_pb2

from deployer.specs import AgentSpec

CREATE = "CREATE"
MODIFY = "MODIFY"
SKIP = "SKIP"
ERROR = "ERROR"


@dataclass
class Result:
    agent_id: str
    source: str
    action: str
    changed_fields: list[str] = field(default_factory=list)
    error: str | None = None


def _tables(ctx) -> list[tuple[str, str, str]]:
    return [
        (t.project_id, t.dataset_id, t.table_id)
        for t in ctx.datasource_references.bq.table_references
    ]


def _examples(ctx) -> list[tuple]:
    return [
        (
            q.natural_language_question.strip(),
            q.sql_query.strip(),
            [(p.name, p.description, p.data_type) for p in q.parameters],
        )
        for q in ctx.example_queries
    ]


def _glossary(ctx) -> list[tuple]:
    return [
        (g.display_name.strip(), g.description.strip(), list(g.labels))
        for g in ctx.glossary_terms
    ]


def _context_diff(existing_ctx, desired_ctx) -> list[str]:
    """Names of the Context fields managed by this repo that differ."""
    checks = {
        "system_instruction": lambda c: c.system_instruction.strip(),
        "table_references": _tables,
        "example_queries": _examples,
        "glossary_terms": _glossary,
        "python_enabled": lambda c: c.options.analysis.python.enabled,
        "model": lambda c: c.options.model,
    }
    return [name for name, get in checks.items() if get(existing_ctx) != get(desired_ctx)]


def diff_data_agent(
    existing: geminidataanalytics.DataAgent,
    desired: geminidataanalytics.DataAgent,
) -> list[str]:
    """Return the names of fields that differ between the live and desired agent.

    display_name / description are only compared when the desired agent sets them,
    so definitions that leave them blank don't overwrite values set elsewhere.
    """
    changed: list[str] = []

    if existing.delete_time:
        changed.append("delete_time (soft-deleted)")

    if desired.display_name and existing.display_name != desired.display_name:
        changed.append("display_name")

    if desired.description and existing.description != desired.description:
        changed.append("description")

    if dict(existing.labels) != dict(desired.labels):
        changed.append("labels")

    existing_agent = existing.data_analytics_agent
    desired_agent = desired.data_analytics_agent
    changed += _context_diff(existing_agent.published_context, desired_agent.published_context)

    # Keep staging_context (what the BigQuery Studio UI displays) in sync with the deployed spec.
    if desired_agent.staging_context != geminidataanalytics.Context() and _context_diff(
        existing_agent.staging_context, desired_agent.staging_context
    ):
        changed.append("staging_context")

    return changed


def update_mask_for(
    desired: geminidataanalytics.DataAgent,
    existing: geminidataanalytics.DataAgent | None = None,
) -> field_mask_pb2.FieldMask:
    paths = []
    if desired.display_name:
        paths.append("display_name")
    if desired.description:
        paths.append("description")
    if desired.labels or (existing is not None and existing.labels):
        paths.append("labels")
    paths.append("data_analytics_agent.published_context")
    if desired.data_analytics_agent.staging_context != geminidataanalytics.Context():
        paths.append("data_analytics_agent.staging_context")
    return field_mask_pb2.FieldMask(paths=paths)


def _update(
    client,
    desired: geminidataanalytics.DataAgent,
    existing: geminidataanalytics.DataAgent | None = None,
) -> None:
    operation = client.update_data_agent(
        request=geminidataanalytics.UpdateDataAgentRequest(
            data_agent=desired, update_mask=update_mask_for(desired, existing)
        )
    )
    operation.result()


def reconcile(client, spec: AgentSpec, dry_run: bool = False) -> Result:
    """Create, update or skip a single Data Agent. Only reads when dry_run is True."""
    desired = spec.data_agent
    try:
        existing = client.get_data_agent(name=desired.name)
    except exceptions.NotFound:
        existing = None

    if existing is None:
        if not dry_run:
            parent = desired.name.rsplit("/dataAgents/", 1)[0]
            try:
                operation = client.create_data_agent(
                    request=geminidataanalytics.CreateDataAgentRequest(
                        parent=parent,
                        data_agent_id=spec.agent_id,
                        data_agent=desired,
                    )
                )
                operation.result()
            except exceptions.AlreadyExists:
                # Soft-deleted agents keep their ID for 30 days; restore by updating in place.
                _update(client, desired)
        return Result(spec.agent_id, spec.source, CREATE)

    changed = diff_data_agent(existing, desired)
    if not changed:
        return Result(spec.agent_id, spec.source, SKIP)

    if not dry_run:
        _update(client, desired, existing)
    return Result(spec.agent_id, spec.source, MODIFY, changed_fields=changed)
