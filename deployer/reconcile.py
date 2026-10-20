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


def _normalized(ctx: geminidataanalytics.Context):
    """Raw protobuf copy of `ctx` with surrounding whitespace trimmed from free-text fields.

    Trimming avoids a perpetual MODIFY if the API normalizes whitespace.
    """
    src = geminidataanalytics.Context.pb(ctx)
    pb = type(src)()
    pb.CopyFrom(src)
    pb.DiscardUnknownFields()
    pb.system_instruction = pb.system_instruction.strip()
    for q in pb.example_queries:
        q.natural_language_question = q.natural_language_question.strip()
        q.sql_query = q.sql_query.strip()
    for g in pb.glossary_terms:
        g.display_name = g.display_name.strip()
        g.description = g.description.strip()
    # BigQuery Studio stores "no limit" as an empty datasource ({}) or a limit of 0;
    # treat both like an unset field.
    if not pb.options.datasource.big_query_max_billed_bytes.value:
        pb.options.ClearField("datasource")
    return pb


def _table_ids(pb) -> list[tuple[str, str, str]]:
    return [(t.project_id, t.dataset_id, t.table_id) for t in pb.datasource_references.bq.table_references]


def _context_diff(existing_ctx, desired_ctx) -> list[str]:
    """Names of every Context field that differs.

    Configs are the complete source of truth, so every field counts, including ones
    the config format can't express (schema relationships, user functions, table
    schema metadata, Looker/Studio sources): those must be empty in the target.
    The broad `datasource_references` and `options` fields are reported by their
    more specific parts where possible.
    """
    existing, desired = _normalized(existing_ctx), _normalized(desired_ctx)
    changed: list[str] = []
    for field in geminidataanalytics.Context.pb().DESCRIPTOR.fields:
        name = field.name
        if getattr(existing, name) == getattr(desired, name):
            continue
        if name == "datasource_references":
            e, d = existing.datasource_references, desired.datasource_references
            if _table_ids(existing) != _table_ids(desired):
                changed.append("table_references")
            elif e.bq != d.bq:
                changed.append("table_schema")
            if e.studio != d.studio or e.looker != d.looker:
                changed.append("datasource_references")
        elif name == "options":
            e, d = existing.options, desired.options
            if e.analysis.python.enabled != d.analysis.python.enabled:
                changed.append("python_enabled")
            if e.model != d.model:
                changed.append("model")
            if e.datasource != d.datasource:
                changed.append("max_billed_bytes")
            rest_e, rest_d = type(e)(), type(d)()
            rest_e.CopyFrom(e)
            rest_d.CopyFrom(d)
            for msg in (rest_e, rest_d):
                msg.analysis.python.ClearField("enabled")
                msg.ClearField("model")
                msg.ClearField("datasource")
            if rest_e != rest_d:
                changed.append("options")
        else:
            changed.append(name)
    return changed


def diff_data_agent(
    existing: geminidataanalytics.DataAgent,
    desired: geminidataanalytics.DataAgent,
) -> list[str]:
    """Return the names of fields that differ between the live and desired agent.

    The desired agent is the complete source of truth: an empty display_name,
    description or label set in the definition means the live value must be empty
    too. Server-managed fields (timestamps, last_published_context, kms_key) are
    ignored.
    """
    changed: list[str] = []

    if existing.delete_time:
        changed.append("delete_time (soft-deleted)")

    if existing.display_name != desired.display_name:
        changed.append("display_name")

    if existing.description != desired.description:
        changed.append("description")

    if dict(existing.labels) != dict(desired.labels):
        changed.append("labels")

    existing_agent = existing.data_analytics_agent
    desired_agent = desired.data_analytics_agent
    changed += _context_diff(existing_agent.published_context, desired_agent.published_context)

    # staging_context is what BigQuery Studio displays; keep it identical to the deployed spec.
    if _context_diff(existing_agent.staging_context, desired_agent.staging_context):
        changed.append("staging_context")

    return changed


# Every field the deployer owns is always written, so unset values clear drift.
UPDATE_MASK_PATHS = (
    "display_name",
    "description",
    "labels",
    "data_analytics_agent.published_context",
    "data_analytics_agent.staging_context",
)


def update_mask_for(desired: geminidataanalytics.DataAgent | None = None) -> field_mask_pb2.FieldMask:
    return field_mask_pb2.FieldMask(paths=list(UPDATE_MASK_PATHS))


def _update(client, desired: geminidataanalytics.DataAgent) -> None:
    operation = client.update_data_agent(
        request=geminidataanalytics.UpdateDataAgentRequest(
            data_agent=desired, update_mask=update_mask_for(desired)
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
        _update(client, desired)
    return Result(spec.agent_id, spec.source, MODIFY, changed_fields=changed)
