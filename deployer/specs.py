"""Load agent definitions (no-code A2A cards and low-code Python modules) into AgentSpecs."""

from __future__ import annotations

import glob
import importlib.util
import json
import os
from dataclasses import dataclass

from google.cloud import geminidataanalytics

DEFAULT_DESCRIPTION = "Enterprise data agent for natural language BigQuery reporting."
BQ_EXTENSION_DESCRIPTION = "BQ Dataset Information"


class SpecError(ValueError):
    """Raised when an agent definition is malformed."""


@dataclass
class AgentSpec:
    agent_id: str
    source: str
    data_agent: geminidataanalytics.DataAgent
    # Original A2A card for no-code agents (triggers Agent Registry sync); None for low-code.
    card_data: dict | None = None


def agent_resource_name(project_id: str, location: str, agent_id: str) -> str:
    return f"projects/{project_id}/locations/{location}/dataAgents/{agent_id}"


# ---------------------------------------------------------------------------
# No-code agents (A2A agent cards)
# ---------------------------------------------------------------------------

def _query_parameter(raw, source: str) -> geminidataanalytics.QueryParameter:
    """Card parameters may be a bare name string or a {name, description, data_type} dict."""
    if isinstance(raw, str):
        return geminidataanalytics.QueryParameter(name=raw)
    if isinstance(raw, dict):
        unknown = set(raw) - {"name", "description", "data_type"}
        if unknown:
            raise SpecError(f"Agent card '{source}': unknown query parameter keys {sorted(unknown)}.")
        return geminidataanalytics.QueryParameter(**raw)
    raise SpecError(f"Agent card '{source}': query parameter must be a string or object, got {raw!r}.")


def parse_agent_card(
    data: dict, source: str, project_id: str, location: str
) -> AgentSpec:
    card_name = data.get("name")
    if not card_name:
        raise SpecError(f"Agent card '{source}' is missing required 'name' field.")

    agent_id = f"{card_name}_no_code_agent"

    extension = next(
        (
            ext
            for ext in (data.get("capabilities") or {}).get("extensions", [])
            if ext.get("description") == BQ_EXTENSION_DESCRIPTION
        ),
        None,
    )
    if extension is None:
        raise SpecError(
            f"Agent card '{source}' has no '{BQ_EXTENSION_DESCRIPTION}' extension, so it carries no "
            "tables, instructions or example queries. Cards copied from the BigQuery UI omit it; "
            "export the card with: python -m deployer.export_card --source <agent> --out <file>"
        )
    params = extension.get("params", {}) or {}
    if not params.get("table_info"):
        raise SpecError(
            f"Agent card '{source}': '{BQ_EXTENSION_DESCRIPTION}' lists no tables (params.table_info)."
        )

    example_queries = []
    for nl_prompt, query_data in (params.get("example_queries") or {}).items():
        # List format: [sql_query, *optional_parameters]
        if isinstance(query_data, list) and query_data:
            sql, raw_parameters = query_data[0], query_data[1:]
        else:
            sql, raw_parameters = str(query_data), []
        example_queries.append(
            geminidataanalytics.ExampleQuery(
                natural_language_question=nl_prompt,
                sql_query=sql,
                parameters=[_query_parameter(p, source) for p in raw_parameters],
            )
        )

    table_references = []
    for table in params.get("table_info") or []:
        parts = table.split(".", 2)
        if len(parts) != 3:
            raise SpecError(
                f"Agent card '{source}': table '{table}' must be 'project.dataset.table'."
            )
        proj_id, ds_id, tbl_id = parts
        table_references.append(
            geminidataanalytics.BigQueryTableReference(
                project_id=proj_id, dataset_id=ds_id, table_id=tbl_id
            )
        )

    published_context = geminidataanalytics.Context(
        system_instruction=params.get("system_instructions", ""),
        datasource_references=geminidataanalytics.DatasourceReferences(
            bq=geminidataanalytics.BigQueryTableReferences(
                table_references=table_references
            )
        ),
        example_queries=example_queries,
        options=geminidataanalytics.ConversationOptions(
            analysis=geminidataanalytics.AnalysisOptions(
                python=geminidataanalytics.AnalysisOptions.Python(enabled=False)
            )
        ),
    )

    data_agent = geminidataanalytics.DataAgent(
        name=agent_resource_name(project_id, location, agent_id),
        display_name=agent_id,
        description=data.get("description") or DEFAULT_DESCRIPTION,
        data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
            published_context=published_context
        ),
    )
    return AgentSpec(
        agent_id=agent_id, source=source, data_agent=data_agent, card_data=data
    )


def load_no_code_specs(directory: str, project_id: str, location: str) -> list[AgentSpec]:
    specs = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise SpecError(f"Agent card '{path}' is not valid JSON: {e}") from e
        specs.append(parse_agent_card(data, path, project_id, location))
    return specs


# ---------------------------------------------------------------------------
# Low-code agents (Python modules exposing AGENT_ID + build_agent)
# ---------------------------------------------------------------------------

def _import_module_from_path(path: str):
    module_name = f"_low_code_agent_{os.path.splitext(os.path.basename(path))[0]}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise SpecError(f"Could not import low-code agent module '{path}'.")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def load_low_code_spec(path: str, project_id: str, location: str) -> AgentSpec:
    module = _import_module_from_path(path)

    agent_id = getattr(module, "AGENT_ID", None)
    if not isinstance(agent_id, str) or not agent_id:
        raise SpecError(f"Low-code agent '{path}' must define a non-empty AGENT_ID string.")

    build_agent = getattr(module, "build_agent", None)
    if not callable(build_agent):
        raise SpecError(
            f"Low-code agent '{path}' must define build_agent(project_id, location)."
        )

    data_agent = build_agent(project_id, location)
    if not isinstance(data_agent, geminidataanalytics.DataAgent):
        raise SpecError(
            f"Low-code agent '{path}': build_agent() must return a geminidataanalytics.DataAgent."
        )

    expected_name = agent_resource_name(project_id, location, agent_id)
    if not data_agent.name:
        data_agent.name = expected_name
    elif data_agent.name != expected_name:
        raise SpecError(
            f"Low-code agent '{path}': DataAgent.name '{data_agent.name}' does not match "
            f"AGENT_ID (expected '{expected_name}'). Leave name unset to have it filled in."
        )

    return AgentSpec(agent_id=agent_id, source=path, data_agent=data_agent)


def load_low_code_specs(directory: str, project_id: str, location: str) -> list[AgentSpec]:
    return [
        load_low_code_spec(path, project_id, location)
        for path in sorted(glob.glob(os.path.join(directory, "*.py")))
        if not os.path.basename(path).startswith("_")
    ]


# ---------------------------------------------------------------------------

def validate(specs: list[AgentSpec]) -> None:
    seen: dict[str, str] = {}
    for spec in specs:
        if spec.agent_id in seen:
            raise SpecError(
                f"Duplicate agent ID '{spec.agent_id}' defined in '{seen[spec.agent_id]}' "
                f"and '{spec.source}'."
            )
        seen[spec.agent_id] = spec.source
