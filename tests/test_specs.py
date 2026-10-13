import json
import os
import textwrap

import pytest
from google.cloud import geminidataanalytics

from deployer import specs

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P, L = "my-project", "global"


def card(**params):
    return {
        "name": "demo",
        "description": "Demo agent",
        "capabilities": {
            "extensions": [
                {"description": "Something else", "params": {}},
                {"description": "BQ Dataset Information", "params": params},
            ]
        },
    }


def test_parse_card_fields():
    spec = specs.parse_agent_card(
        card(
            system_instructions="Be helpful.",
            table_info=["p1.d1.t1", "p2.d2.t2"],
            example_queries={
                "q1": ["SELECT 1", "param_a", {"name": "param_b", "data_type": "STRING"}],
                "q2": "SELECT 2",
            },
        ),
        "demo.json", P, L,
    )
    agent = spec.data_agent
    ctx = agent.data_analytics_agent.published_context

    assert spec.agent_id == "demo_no_code_agent"
    assert agent.name == f"projects/{P}/locations/{L}/dataAgents/demo_no_code_agent"
    assert agent.display_name == "demo_no_code_agent"
    assert agent.description == "Demo agent"
    assert ctx.system_instruction == "Be helpful."
    assert [(t.project_id, t.dataset_id, t.table_id) for t in ctx.datasource_references.bq.table_references] == [
        ("p1", "d1", "t1"), ("p2", "d2", "t2"),
    ]
    assert [
        (q.natural_language_question, q.sql_query, [(p.name, p.data_type) for p in q.parameters])
        for q in ctx.example_queries
    ] == [
        ("q1", "SELECT 1", [("param_a", ""), ("param_b", "STRING")]),
        ("q2", "SELECT 2", []),
    ]
    assert spec.card_data["name"] == "demo"


def test_parse_card_rejects_unknown_parameter_keys():
    with pytest.raises(specs.SpecError, match="unknown query parameter keys"):
        specs.parse_agent_card(card(example_queries={"q": ["SELECT 1", {"nme": "typo"}]}), "bad.json", P, L)


def test_parse_card_defaults_description():
    data = card()
    del data["description"]
    assert specs.parse_agent_card(data, "x.json", P, L).data_agent.description == specs.DEFAULT_DESCRIPTION


def test_parse_card_requires_name():
    with pytest.raises(specs.SpecError, match="name"):
        specs.parse_agent_card({"description": "x"}, "bad.json", P, L)


def test_parse_card_rejects_bad_table():
    with pytest.raises(specs.SpecError, match="project.dataset.table"):
        specs.parse_agent_card(card(table_info=["only.two"]), "bad.json", P, L)


def test_repo_agent_cards_parse():
    loaded = specs.load_no_code_specs(os.path.join(REPO_ROOT, "agent_cards"), P, L)
    assert loaded, "expected at least one card in agent_cards/"
    specs.validate(loaded)


def test_repo_low_code_agents_load():
    loaded = specs.load_low_code_specs(os.path.join(REPO_ROOT, "low_code_agents"), P, L)
    assert loaded, "expected at least one module in low_code_agents/"
    for spec in loaded:
        assert spec.card_data is None
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


def test_low_code_loader_ignores_private_modules(tmp_path):
    write(tmp_path, "_helpers.py", "X = 1\n")
    assert specs.load_low_code_specs(str(tmp_path), P, L) == []


@pytest.mark.parametrize("body, match", [
    ("def build_agent(p, l): pass\n", "AGENT_ID"),
    ("AGENT_ID = 'a'\n", "build_agent"),
    ("AGENT_ID = 'a'\ndef build_agent(p, l): return {}\n", "DataAgent"),
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


def test_invalid_json(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(specs.SpecError, match="not valid JSON"):
        specs.load_no_code_specs(str(tmp_path), P, L)


def test_validate_rejects_duplicates():
    a = specs.AgentSpec("dup", "a.json", geminidataanalytics.DataAgent())
    b = specs.AgentSpec("dup", "b.py", geminidataanalytics.DataAgent())
    with pytest.raises(specs.SpecError, match="Duplicate"):
        specs.validate([a, b])
