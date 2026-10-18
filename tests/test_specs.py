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

import json
import os
import textwrap

import pytest
from google.cloud import geminidataanalytics

from deployer import specs

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P, L = "my-project", "global"


def config(**overrides):
    data = {
        "agent_id": "demo_agent",
        "display_name": "Demo agent",
        "description": "Demo description",
        "table_references": [{"project_id": "p", "dataset_id": "d", "table_id": "t"}],
    }
    data.update(overrides)
    return data


def tables(spec):
    ctx = spec.data_agent.data_analytics_agent.published_context
    return [(t.project_id, t.dataset_id, t.table_id) for t in ctx.datasource_references.bq.table_references]


# ---------------------------------------------------------------- no-code configs


def test_parse_config_fields():
    spec = specs.parse_agent_config(
        config(
            system_instruction="Be helpful.",
            table_references=[
                {"project_id": "p1", "dataset_id": "d1", "table_id": "t1"},
                {"project_id": "p2", "dataset_id": "d2", "table_id": "t2"},
            ],
            example_queries=[
                {
                    "natural_language_question": "q1",
                    "sql_query": "SELECT 1",
                    "parameters": [{"name": "param_b", "data_type": "STRING"}],
                },
                {"natural_language_question": "q2", "sql_query": "SELECT 2"},
            ],
            glossary_terms=[{"display_name": "Revenue", "description": "Money in", "labels": ["finance"]}],
            options={"python_enabled": True},
        ),
        "demo.json", P, L,
    )
    agent = spec.data_agent
    ctx = agent.data_analytics_agent.published_context

    assert spec.agent_id == "demo_agent"
    assert spec.register is True
    assert agent.name == f"projects/{P}/locations/{L}/dataAgents/demo_agent"
    assert agent.display_name == "Demo agent"
    assert agent.description == "Demo description"
    assert ctx.system_instruction == "Be helpful."
    assert tables(spec) == [("p1", "d1", "t1"), ("p2", "d2", "t2")]
    assert [
        (q.natural_language_question, q.sql_query, [(p.name, p.data_type) for p in q.parameters])
        for q in ctx.example_queries
    ] == [("q1", "SELECT 1", [("param_b", "STRING")]), ("q2", "SELECT 2", [])]
    assert [(g.display_name, g.description, list(g.labels)) for g in ctx.glossary_terms] == [
        ("Revenue", "Money in", ["finance"]),
    ]
    assert ctx.options.analysis.python.enabled is True


@pytest.mark.parametrize("options, expected", [
    ({"max_billed_bytes": 10**9}, 10**9),
    ({"max_billed_bytes": 0}, None),    # 0 means no limit, so the setting is left unset
    ({"max_billed_bytes": None}, None),
    ({}, None),
])
def test_max_billed_bytes(options, expected):
    spec = specs.parse_agent_config(config(options=options), "bytes.json", P, L)
    opts = spec.data_agent.data_analytics_agent.published_context.options
    assert opts.datasource.big_query_max_billed_bytes == expected
    assert ("datasource" in opts) is (expected is not None)


def test_parse_config_defaults():
    spec = specs.parse_agent_config(
        {"agent_id": "a", "table_references": [{"project_id": "p", "dataset_id": "d", "table_id": "t"}]},
        "min.json", P, L,
    )
    ctx = spec.data_agent.data_analytics_agent.published_context
    assert spec.data_agent.display_name == ""
    assert spec.data_agent.description == ""
    assert ctx.system_instruction == ""
    assert list(ctx.example_queries) == []
    assert ctx.options.analysis.python.enabled is False


def test_source_project_remapped_to_target():
    spec = specs.parse_agent_config(
        config(
            source_project_id="dev-proj",
            system_instruction="Use `dev-proj.sales.orders`.",
            table_references=[
                {"project_id": "dev-proj", "dataset_id": "sales", "table_id": "orders"},
                {"project_id": "bigquery-public-data", "dataset_id": "pub", "table_id": "t"},
            ],
            example_queries=[{
                "natural_language_question": "q",
                "sql_query": "SELECT * FROM `dev-proj.sales.orders` JOIN `team-dev-proj.x.y` USING (id)",
            }],
        ),
        "remap.json", "prd-proj", L,
    )
    ctx = spec.data_agent.data_analytics_agent.published_context
    assert tables(spec) == [("prd-proj", "sales", "orders"), ("bigquery-public-data", "pub", "t")]
    assert ctx.example_queries[0].sql_query == (
        "SELECT * FROM `prd-proj.sales.orders` JOIN `team-dev-proj.x.y` USING (id)"
    )
    assert ctx.system_instruction == "Use `prd-proj.sales.orders`."


@pytest.mark.parametrize("sql, expected", [
    # Rewritten: the source project in the project position, however it's quoted.
    ("SELECT * FROM dev-proj.sales.orders", "SELECT * FROM prd-proj.sales.orders"),
    ("SELECT * FROM `dev-proj.sales.orders`", "SELECT * FROM `prd-proj.sales.orders`"),
    ("SELECT * FROM `dev-proj`.`sales`.`orders`", "SELECT * FROM `prd-proj`.`sales`.`orders`"),  # BigQuery Studio style
    ("SELECT * FROM `dev-proj`.sales.orders", "SELECT * FROM `prd-proj`.sales.orders"),
    ("FROM `dev-proj.a.b` JOIN `dev-proj`.`c`.`d`", "FROM `prd-proj.a.b` JOIN `prd-proj`.`c`.`d`"),
    # Left alone: other projects, longer IDs, and datasets/tables that share the name.
    ("SELECT * FROM `team-dev-proj.x.y`", "SELECT * FROM `team-dev-proj.x.y`"),
    ("SELECT * FROM dev-proj-2.x.y", "SELECT * FROM dev-proj-2.x.y"),
    ("SELECT * FROM other.dev-proj.t", "SELECT * FROM other.dev-proj.t"),
    ("SELECT * FROM `other`.`dev-proj`.`t`", "SELECT * FROM `other`.`dev-proj`.`t`"),
    ("SELECT * FROM `other.dev-proj.t`", "SELECT * FROM `other.dev-proj.t`"),
])
def test_source_project_rewrite_handles_quoting_styles(sql, expected):
    spec = specs.parse_agent_config(
        config(
            source_project_id="dev-proj",
            system_instruction=sql,
            example_queries=[{"natural_language_question": "q", "sql_query": sql}],
        ),
        "rewrite.json", "prd-proj", L,
    )
    ctx = spec.data_agent.data_analytics_agent.published_context
    assert ctx.example_queries[0].sql_query == expected
    assert ctx.system_instruction == expected


def test_config_location_overrides_default():
    spec = specs.parse_agent_config(config(location="US"), "us.json", P, "global")
    assert spec.location == "us"
    assert spec.data_agent.name == f"projects/{P}/locations/us/dataAgents/demo_agent"


def test_config_without_location_uses_cli_default():
    spec = specs.parse_agent_config(config(), "eu.json", P, "EU")
    assert spec.location == "eu"
    assert spec.data_agent.name == f"projects/{P}/locations/eu/dataAgents/demo_agent"


@pytest.mark.parametrize("location", ["", "   ", 5, ["us"]])
def test_invalid_config_location_rejected(location):
    with pytest.raises(specs.SpecError, match="location must be a non-empty string"):
        specs.parse_agent_config(config(location=location), "bad.json", P, L)


def test_source_project_id_is_stripped():
    spec = specs.parse_agent_config(
        config(
            source_project_id=" dev-proj ",
            table_references=[{"project_id": "dev-proj", "dataset_id": "d", "table_id": "t"}],
        ),
        "strip.json", "prd-proj", L,
    )
    assert tables(spec) == [("prd-proj", "d", "t")]


def test_source_project_equal_to_target_is_noop():
    spec = specs.parse_agent_config(
        config(source_project_id=P, table_references=[{"project_id": P, "dataset_id": "d", "table_id": "t"}]),
        "same.json", P, L,
    )
    assert tables(spec) == [(P, "d", "t")]


@pytest.mark.parametrize("agent_id", ["", "My Agent", "1agent", "agent.v2", "a" * 64, "abc\n", 42, None])
def test_invalid_agent_id_rejected(agent_id):
    with pytest.raises(specs.SpecError):
        specs.parse_agent_config(config(agent_id=agent_id), "bad.json", P, L)


def test_null_optional_strings_become_empty():
    spec = specs.parse_agent_config(
        config(
            description=None,
            example_queries=[{
                "natural_language_question": "q", "sql_query": "s",
                "parameters": [{"name": "n", "description": None}],
            }],
            glossary_terms=[{"display_name": "t", "description": None}],
        ),
        "nulls.json", P, L,
    )
    ctx = spec.data_agent.data_analytics_agent.published_context
    assert spec.data_agent.description == ""
    assert ctx.example_queries[0].parameters[0].description == ""
    assert ctx.glossary_terms[0].description == ""


@pytest.mark.parametrize("overrides, match", [
    ({"agent_name": "typo"}, "unknown keys"),
    ({"table_references": []}, "at least one table"),
    ({"table_references": [{"project_id": "p", "dataset_id": "d"}]}, "table_id"),
    ({"table_references": [{"project_id": "p", "dataset_id": "d", "table_id": "t", "schema": {}}]}, "unknown keys"),
    ({"table_references": "p.d.t"}, "must be a list"),
    ({"example_queries": [{"natural_language_question": "q"}]}, "sql_query"),
    ({"example_queries": [{"natural_language_question": "q", "sql_query": "s", "parameters": [{"nme": "x"}]}]}, "unknown keys"),
    ({"example_queries": [{"natural_language_question": "q", "sql_query": "s", "parameters": [{"name": "x", "data_type": 5}]}]}, "data_type must be a string"),
    ({"glossary_terms": [{"description": "no name"}]}, "display_name"),
    ({"glossary_terms": [{"display_name": "t", "description": ["x"]}]}, "description must be a string"),
    ({"glossary_terms": [{"display_name": "t", "labels": [1]}]}, "list of strings"),
    ({"options": {"python": True}}, "unknown keys"),
    ({"options": {"python_enabled": "yes"}}, "true or false"),
    ({"options": {"model": "GPT_4"}}, "options.model must be one of"),
    ({"options": {"max_billed_bytes": -1}}, "max_billed_bytes must be a whole number"),
    ({"options": {"max_billed_bytes": "1000"}}, "max_billed_bytes must be a whole number"),
    ({"options": {"max_billed_bytes": 1.5}}, "max_billed_bytes must be a whole number"),
    ({"options": {"max_billed_bytes": True}}, "max_billed_bytes must be a whole number"),
    ({"options": []}, "must be an object"),
    ({"options": False}, "must be an object"),
    ({"labels": ["not-a-dict"]}, "labels must be an object"),
    ({"labels": {"k": 1}}, "labels must be an object"),
    ({"source_project_id": ""}, "source_project_id must be a non-empty string"),
    ({"source_project_id": "   "}, "source_project_id must be a non-empty string"),
    ({"source_project_id": 7}, "source_project_id must be a non-empty string"),
    ({"description": 3}, "must be a string"),
])
def test_invalid_config_rejected(overrides, match):
    with pytest.raises(specs.SpecError, match=match):
        specs.parse_agent_config(config(**overrides), "bad.json", P, L)


def test_config_must_be_object():
    with pytest.raises(specs.SpecError, match="must be an object"):
        specs.parse_agent_config([], "bad.json", P, L)


def test_loader_skips_underscore_files_and_rejects_bad_json(tmp_path):
    (tmp_path / "_template.json").write_text("{not even json")
    (tmp_path / "ok.json").write_text(json.dumps(config()))
    (spec,) = specs.load_agent_configs(str(tmp_path), P, L)
    assert spec.agent_id == "demo_agent"

    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(specs.SpecError, match="not valid JSON"):
        specs.load_agent_configs(str(tmp_path), P, L)


# The repo-content tests validate whatever definitions exist. They skip rather than fail
# when a folder is empty or missing, so forks that delete the samples still pass CI.


def test_repo_agent_configs_parse():
    loaded = specs.load_agent_configs(os.path.join(REPO_ROOT, "agent_configs"), P, L)
    if not loaded:
        pytest.skip("no configs in agent_configs/")
    specs.validate(loaded)


def test_repo_template_has_only_known_keys():
    path = os.path.join(REPO_ROOT, "agent_configs", "_template.json")
    if not os.path.exists(path):
        pytest.skip("agent_configs/_template.json not present")
    with open(path, encoding="utf-8") as f:
        template = json.load(f)
    assert set(template) == specs.CONFIG_KEYS


# ---------------------------------------------------------------- low-code modules


def test_repo_low_code_agents_load():
    loaded = specs.load_low_code_specs(os.path.join(REPO_ROOT, "low_code_agents"), P, L)
    if not loaded:
        pytest.skip("no modules in low_code_agents/")
    for spec in loaded:
        assert spec.register is False
        assert spec.data_agent.name.endswith(f"/dataAgents/{spec.agent_id}")


def write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return tmp_path


def test_low_code_loader_fills_name(tmp_path):
    write(tmp_path, "ok.py", """
        from google.cloud import geminidataanalytics
        AGENT_ID = "my_agent"
        def build_agent(project_id, location):
            return geminidataanalytics.DataAgent(description="d")
    """)
    (spec,) = specs.load_low_code_specs(str(tmp_path), P, L)
    assert spec.agent_id == "my_agent"
    assert spec.data_agent.name == f"projects/{P}/locations/{L}/dataAgents/my_agent"


LOW_CODE_WITH_LOCATION = """
    from google.cloud import geminidataanalytics
    AGENT_ID = "regional_agent"
    {location_line}
    seen = []
    def build_agent(project_id, location):
        seen.append(location)
        return geminidataanalytics.DataAgent(description=location)
"""


def test_low_code_location_overrides_default(tmp_path):
    write(tmp_path, "regional.py", LOW_CODE_WITH_LOCATION.format(location_line='LOCATION = " US "'))
    (spec,) = specs.load_low_code_specs(str(tmp_path), P, "global")
    assert spec.location == "us"
    assert spec.data_agent.name == f"projects/{P}/locations/us/dataAgents/regional_agent"
    assert spec.data_agent.description == "us"  # build_agent receives the resolved location


def test_low_code_without_location_uses_cli_default(tmp_path):
    write(tmp_path, "regional.py", LOW_CODE_WITH_LOCATION.format(location_line=""))
    (spec,) = specs.load_low_code_specs(str(tmp_path), P, "EU")
    assert spec.location == "eu"
    assert spec.data_agent.name == f"projects/{P}/locations/eu/dataAgents/regional_agent"


@pytest.mark.parametrize("location_line", ['LOCATION = ""', 'LOCATION = "  "', "LOCATION = 5"])
def test_low_code_invalid_location_rejected(tmp_path, location_line):
    write(tmp_path, "regional.py", LOW_CODE_WITH_LOCATION.format(location_line=location_line))
    with pytest.raises(specs.SpecError, match="LOCATION must be a non-empty string"):
        specs.load_low_code_specs(str(tmp_path), P, L)


def test_low_code_own_staging_context_is_kept(tmp_path):
    write(tmp_path, "staged.py", """
        from google.cloud import geminidataanalytics as g
        AGENT_ID = "staged_agent"
        def build_agent(project_id, location):
            return g.DataAgent(data_analytics_agent=g.DataAnalyticsAgent(
                published_context=g.Context(system_instruction="published"),
                staging_context=g.Context(system_instruction="draft"),
            ))
    """)
    (spec,) = specs.load_low_code_specs(str(tmp_path), P, L)
    assert spec.data_agent.data_analytics_agent.staging_context.system_instruction == "draft"


def test_low_code_loader_ignores_private_modules(tmp_path):
    write(tmp_path, "_helpers.py", "X = 1\n")
    assert specs.load_low_code_specs(str(tmp_path), P, L) == []


@pytest.mark.parametrize("body, match", [
    ("def build_agent(p, l): pass\n", "agent_id"),
    ("AGENT_ID = 'Bad Id'\n", "agent_id"),
    ("AGENT_ID = 'a'\n", "build_agent"),
    ("AGENT_ID = 'a'\ndef build_agent(p, l): return {}\n", "DataAgent"),
    ("import does_not_exist_xyz\n", "failed to import: ModuleNotFoundError"),
    ("AGENT_ID = 'a'\ndef build_agent(p, l): raise KeyError('boom')\n", r"build_agent\(\) raised KeyError"),
])
def test_low_code_loader_errors(tmp_path, body, match):
    write(tmp_path, "bad.py", body)
    with pytest.raises(specs.SpecError, match=match):
        specs.load_low_code_specs(str(tmp_path), P, L)


def test_low_code_loader_rejects_mismatched_name(tmp_path):
    write(tmp_path, "bad.py", """
        from google.cloud import geminidataanalytics
        AGENT_ID = "a"
        def build_agent(project_id, location):
            return geminidataanalytics.DataAgent(name="projects/x/locations/global/dataAgents/b")
    """)
    with pytest.raises(specs.SpecError, match="does not match"):
        specs.load_low_code_specs(str(tmp_path), P, L)


def test_validate_rejects_duplicates():
    a = specs.AgentSpec("dup", "a.json", geminidataanalytics.DataAgent())
    b = specs.AgentSpec("dup", "b.py", geminidataanalytics.DataAgent())
    with pytest.raises(specs.SpecError, match="Duplicate"):
        specs.validate([a, b])


def test_validate_rejects_registry_service_id_collisions():
    a = specs.AgentSpec("sales_agent", "a.json", geminidataanalytics.DataAgent(), register=True)
    b = specs.AgentSpec("sales-agent", "b.json", geminidataanalytics.DataAgent(), register=True)
    with pytest.raises(specs.SpecError, match="same Agent Registry service 'sales-agent'"):
        specs.validate([a, b])
    # Low-code agents aren't registered, so they can't collide.
    specs.validate([a, specs.AgentSpec("sales-agent", "b.py", geminidataanalytics.DataAgent())])


def test_repo_low_code_agents_keep_staging_in_sync():
    for spec in specs.load_low_code_specs(os.path.join(REPO_ROOT, "low_code_agents"), P, L):
        analytics = spec.data_agent.data_analytics_agent
        assert analytics.staging_context == analytics.published_context
