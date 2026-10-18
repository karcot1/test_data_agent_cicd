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

"""Load agent definitions (no-code JSON configs and low-code Python modules) into AgentSpecs.

No-code agents are declarative JSON files in agent_configs/ (see agent_configs/_template.json):

    {
      "agent_id": "sales_agent",                      # required, Data Agent ID
      "location": "global",                           # optional, defaults to CLI --location ("global")
      "display_name": "Sales agent",
      "description": "Answers questions about sales.",
      "labels": {"env": "prod"},
      "source_project_id": "my-dev-project",          # optional, see below
      "system_instruction": "...",
      "table_references": [                           # required, at least one
        {"project_id": "...", "dataset_id": "...", "table_id": "..."}
      ],
      "example_queries": [
        {"natural_language_question": "...", "sql_query": "...",
         "parameters": [{"name": "...", "description": "...", "data_type": "..."}]}
      ],
      "glossary_terms": [{"display_name": "...", "description": "...", "labels": ["..."]}],
      "options": {"python_enabled": false, "model": "MODEL_UNSPECIFIED",
                  "max_billed_bytes": 0}             # 0 or omitted = no limit
    }

When source_project_id is set and differs from the target project, every reference to
that project (table references, example SQL and the system instruction) is rewritten to
the target project. References to any other project, such as bigquery-public-data, are
left untouched.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
from dataclasses import dataclass

from google.cloud import geminidataanalytics

from deployer.registry import service_id_for

# Data Agent IDs accepted by the API: a lowercase letter, then up to 62 lowercase
# letters, digits, underscores or hyphens.
AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")

CONFIG_KEYS = {
    "agent_id",
    "location",
    "display_name",
    "description",
    "labels",
    "source_project_id",
    "system_instruction",
    "table_references",
    "example_queries",
    "glossary_terms",
    "options",
}
TABLE_KEYS = {"project_id", "dataset_id", "table_id"}
EXAMPLE_QUERY_KEYS = {"natural_language_question", "sql_query", "parameters"}
PARAMETER_KEYS = {"name", "description", "data_type"}
GLOSSARY_TERM_KEYS = {"display_name", "description", "labels"}
OPTION_KEYS = {"python_enabled", "model", "max_billed_bytes"}
VALID_MODELS = {"MODEL_UNSPECIFIED", "LATEST_GA_MODEL"}


class SpecError(ValueError):
    """Raised when an agent definition is malformed."""


@dataclass
class AgentSpec:
    agent_id: str
    source: str
    data_agent: geminidataanalytics.DataAgent
    # No-code agents are also published to Agent Registry; low-code agents are not.
    register: bool = False
    location: str = "global"


def agent_resource_name(project_id: str, location: str, agent_id: str) -> str:
    return f"projects/{project_id}/locations/{location}/dataAgents/{agent_id}"


def validate_agent_id(agent_id, source: str) -> str:
    if not isinstance(agent_id, str) or not AGENT_ID_RE.fullmatch(agent_id):
        raise SpecError(
            f"'{source}': agent_id {agent_id!r} must start with a lowercase letter and contain "
            "only lowercase letters, digits, '_' or '-' (max 63 characters)."
        )
    return agent_id


# ---------------------------------------------------------------------------
# No-code agents (declarative JSON configs)
# ---------------------------------------------------------------------------

def _check_object(obj, allowed: set[str], required: set[str], where: str) -> dict:
    if not isinstance(obj, dict):
        raise SpecError(f"{where} must be an object, got {type(obj).__name__}.")
    unknown = set(obj) - allowed
    if unknown:
        raise SpecError(f"{where} has unknown keys {sorted(unknown)}; allowed: {sorted(allowed)}.")
    for key in sorted(required):
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            raise SpecError(f"{where} requires a non-empty string '{key}'.")
    return obj


def _check_list(value, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError(f"{where} must be a list, got {type(value).__name__}.")
    return value


def _optional_str(obj: dict, key: str, where: str) -> str:
    """Return obj[key] as a string; a missing or null value becomes ""."""
    value = obj.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SpecError(f"{where}.{key} must be a string, got {type(value).__name__}.")
    return value


def _project_rewriter(source_project_id: str | None, target_project_id: str):
    """Return a function that rewrites references to `source_project_id` to the target project.

    Handles `project.dataset.table`, the whole reference quoted (`project.dataset.table`)
    and per-identifier quoting (`project`.`dataset`.`table`, which BigQuery Studio
    generates). The source ID is only rewritten in the project position: not inside a
    longer ID (e.g. "my-dev" in "team-my-dev") and not after a dot, where it would be a
    dataset or table that happens to share the name. The legacy project:dataset form is
    left unchanged.
    """
    if not source_project_id or source_project_id == target_project_id:
        return lambda text: text
    pattern = re.compile(
        r"(?<![A-Za-z0-9_.\-])(?<!\.`)"  # start of an identifier, not a dataset/table position
        rf"(`?){re.escape(source_project_id)}(`?)\."
    )
    return lambda text: pattern.sub(rf"\g<1>{target_project_id}\g<2>.", text)


def parse_agent_config(data, source: str, project_id: str, location: str = "global") -> AgentSpec:
    """Validate a JSON agent config and build the desired DataAgent for `project_id`."""
    _check_object(data, CONFIG_KEYS, {"agent_id"}, f"Agent config '{source}'")
    agent_id = validate_agent_id(data["agent_id"], source)

    raw_location = data.get("location")
    if raw_location is None:
        agent_location = location.lower()
    elif isinstance(raw_location, str) and raw_location.strip():
        agent_location = raw_location.strip().lower()
    else:
        raise SpecError(f"'{source}': location must be a non-empty string.")

    source_project_id = data.get("source_project_id")
    if source_project_id is not None:
        if not isinstance(source_project_id, str) or not source_project_id.strip():
            raise SpecError(f"'{source}': source_project_id must be a non-empty string.")
        source_project_id = source_project_id.strip()
    rewrite = _project_rewriter(source_project_id, project_id)

    tables = _check_list(data.get("table_references"), f"'{source}': table_references")
    if not tables:
        raise SpecError(f"'{source}': table_references must list at least one table.")
    table_references = []
    for i, table in enumerate(tables):
        _check_object(table, TABLE_KEYS, TABLE_KEYS, f"'{source}': table_references[{i}]")
        table_project = table["project_id"]
        if table_project == source_project_id:
            table_project = project_id
        table_references.append(
            geminidataanalytics.BigQueryTableReference(
                project_id=table_project,
                dataset_id=table["dataset_id"],
                table_id=table["table_id"],
            )
        )

    example_queries = []
    for i, query in enumerate(_check_list(data.get("example_queries"), f"'{source}': example_queries")):
        where = f"'{source}': example_queries[{i}]"
        _check_object(query, EXAMPLE_QUERY_KEYS, {"natural_language_question", "sql_query"}, where)
        parameters = []
        for j, param in enumerate(_check_list(query.get("parameters"), f"{where}.parameters")):
            param_where = f"{where}.parameters[{j}]"
            _check_object(param, PARAMETER_KEYS, {"name"}, param_where)
            parameters.append(
                geminidataanalytics.QueryParameter(
                    name=param["name"],
                    description=_optional_str(param, "description", param_where),
                    data_type=_optional_str(param, "data_type", param_where),
                )
            )
        example_queries.append(
            geminidataanalytics.ExampleQuery(
                natural_language_question=query["natural_language_question"],
                sql_query=rewrite(query["sql_query"]),
                parameters=parameters,
            )
        )

    glossary_terms = []
    for i, term in enumerate(_check_list(data.get("glossary_terms"), f"'{source}': glossary_terms")):
        where = f"'{source}': glossary_terms[{i}]"
        _check_object(term, GLOSSARY_TERM_KEYS, {"display_name"}, where)
        labels = _check_list(term.get("labels"), f"{where}.labels")
        if not all(isinstance(label, str) for label in labels):
            raise SpecError(f"{where}.labels must be a list of strings.")
        glossary_terms.append(
            geminidataanalytics.GlossaryTerm(
                display_name=term["display_name"],
                description=_optional_str(term, "description", where),
                labels=labels,
            )
        )

    options = data.get("options")
    _check_object({} if options is None else options, OPTION_KEYS, set(), f"'{source}': options")
    python_enabled = (options or {}).get("python_enabled", False)
    if not isinstance(python_enabled, bool):
        raise SpecError(f"'{source}': options.python_enabled must be true or false.")
    max_billed_bytes = (options or {}).get("max_billed_bytes")
    if max_billed_bytes is None:
        max_billed_bytes = 0
    # bool is a subclass of int in Python, so exclude it explicitly.
    if isinstance(max_billed_bytes, bool) or not isinstance(max_billed_bytes, int) or max_billed_bytes < 0:
        raise SpecError(
            f"'{source}': options.max_billed_bytes must be a whole number of bytes, 0 or more "
            f"(0 means no limit), got {max_billed_bytes!r}."
        )
    model_name = (options or {}).get("model") or "MODEL_UNSPECIFIED"
    if not isinstance(model_name, str) or model_name not in VALID_MODELS:
        raise SpecError(
            f"'{source}': options.model must be one of {sorted(VALID_MODELS)}, got {model_name!r}."
        )

    raw_labels = data.get("labels")
    if raw_labels is None:
        agent_labels: dict[str, str] = {}
    elif isinstance(raw_labels, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw_labels.items()
    ):
        agent_labels = dict(raw_labels)
    else:
        raise SpecError(f"'{source}': labels must be an object mapping strings to strings.")

    where = f"'{source}'"
    display_name = _optional_str(data, "display_name", where)
    description = _optional_str(data, "description", where)
    system_instruction = _optional_str(data, "system_instruction", where)

    published_context = geminidataanalytics.Context(
        system_instruction=rewrite(system_instruction),
        datasource_references=geminidataanalytics.DatasourceReferences(
            bq=geminidataanalytics.BigQueryTableReferences(table_references=table_references)
        ),
        example_queries=example_queries,
        glossary_terms=glossary_terms,
        options=geminidataanalytics.ConversationOptions(
            analysis=geminidataanalytics.AnalysisOptions(
                python=geminidataanalytics.AnalysisOptions.Python(enabled=python_enabled)
            ),
            model=geminidataanalytics.ConversationOptions.Model[model_name],
            # Only set when there is a limit; unset, {} and 0 all mean "no limit".
            datasource=(
                geminidataanalytics.DatasourceOptions(big_query_max_billed_bytes=max_billed_bytes)
                if max_billed_bytes
                else None
            ),
        ),
    )
    data_agent = geminidataanalytics.DataAgent(
        name=agent_resource_name(project_id, agent_location, agent_id),
        display_name=display_name,
        description=description,
        labels=agent_labels,
        data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
            published_context=published_context,
            staging_context=published_context,
        ),
    )
    return AgentSpec(
        agent_id=agent_id,
        source=source,
        data_agent=data_agent,
        register=True,
        location=agent_location,
    )


def load_agent_configs(directory: str, project_id: str, location: str = "global") -> list[AgentSpec]:
    """Load every *.json in `directory`, except files starting with '_' (e.g. _template.json)."""
    specs = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        if os.path.basename(path).startswith("_"):
            continue
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise SpecError(f"Agent config '{path}' is not valid JSON: {e}") from e
        specs.append(parse_agent_config(data, path, project_id, location))
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
    try:
        module_spec.loader.exec_module(module)
    except Exception as e:
        raise SpecError(f"Low-code agent '{path}' failed to import: {type(e).__name__}: {e}") from e
    return module


def load_low_code_spec(path: str, project_id: str, location: str = "global") -> AgentSpec:
    module = _import_module_from_path(path)

    agent_id = validate_agent_id(getattr(module, "AGENT_ID", None), path)

    raw_location = getattr(module, "LOCATION", None)
    if raw_location is None:
        agent_location = location.lower()
    elif isinstance(raw_location, str) and raw_location.strip():
        agent_location = raw_location.strip().lower()
    else:
        raise SpecError(f"Low-code agent '{path}': LOCATION must be a non-empty string.")

    build_agent = getattr(module, "build_agent", None)
    if not callable(build_agent):
        raise SpecError(
            f"Low-code agent '{path}' must define build_agent(project_id, location)."
        )

    try:
        data_agent = build_agent(project_id, agent_location)
    except Exception as e:
        raise SpecError(
            f"Low-code agent '{path}': build_agent() raised {type(e).__name__}: {e}"
        ) from e
    if not isinstance(data_agent, geminidataanalytics.DataAgent):
        raise SpecError(
            f"Low-code agent '{path}': build_agent() must return a geminidataanalytics.DataAgent."
        )

    expected_name = agent_resource_name(project_id, agent_location, agent_id)
    if not data_agent.name:
        data_agent.name = expected_name
    elif data_agent.name != expected_name:
        raise SpecError(
            f"Low-code agent '{path}': DataAgent.name '{data_agent.name}' does not match "
            f"AGENT_ID (expected '{expected_name}'). Leave name unset to have it filled in."
        )

    # Like JSON configs, keep the draft that BigQuery Studio shows in sync with what's deployed.
    analytics = data_agent.data_analytics_agent
    if analytics.staging_context == geminidataanalytics.Context():
        analytics.staging_context = analytics.published_context

    return AgentSpec(
        agent_id=agent_id, source=path, data_agent=data_agent, location=agent_location
    )


def load_low_code_specs(directory: str, project_id: str, location: str = "global") -> list[AgentSpec]:
    return [
        load_low_code_spec(path, project_id, location)
        for path in sorted(glob.glob(os.path.join(directory, "*.py")))
        if not os.path.basename(path).startswith("_")
    ]


# ---------------------------------------------------------------------------

def validate(specs: list[AgentSpec]) -> None:
    seen: dict[str, str] = {}
    services: dict[str, AgentSpec] = {}
    for spec in specs:
        if spec.agent_id in seen:
            raise SpecError(
                f"Duplicate agent ID '{spec.agent_id}' defined in '{seen[spec.agent_id]}' "
                f"and '{spec.source}'."
            )
        seen[spec.agent_id] = spec.source

        # Agent Registry service IDs replace '_' with '-' and are truncated, so distinct
        # agent IDs can map to the same service and overwrite each other's entry.
        if spec.register:
            service_id = service_id_for(spec.agent_id)
            other = services.setdefault(service_id, spec)
            if other is not spec:
                raise SpecError(
                    f"Agent IDs '{other.agent_id}' ('{other.source}') and '{spec.agent_id}' "
                    f"('{spec.source}') map to the same Agent Registry service '{service_id}'. "
                    "Rename one of them."
                )
