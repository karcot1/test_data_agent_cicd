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

import pytest
from google.api_core import exceptions
from google.cloud import geminidataanalytics

from deployer import export_agent, specs
from deployer.auth import AuthError
from deployer.export_agent import ExportError, agent_to_config, default_agent_id, parse_source


def live_agent(tables=(("dev", "d", "t"),)):
    """Shape of a GetDataAgent response, including server-populated fields."""
    return geminidataanalytics.DataAgent(
        name="projects/dev/locations/us/dataAgents/agent_123",
        display_name="Sales Agent",
        description="Sales questions",
        labels={"env": "dev"},
        data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
            staging_context=geminidataanalytics.Context(system_instruction="draft"),
            published_context=geminidataanalytics.Context(
                system_instruction="Be precise.",
                datasource_references=geminidataanalytics.DatasourceReferences(
                    bq=geminidataanalytics.BigQueryTableReferences(
                        table_references=[
                            geminidataanalytics.BigQueryTableReference(
                                project_id=p, dataset_id=d, table_id=t,
                                schema=geminidataanalytics.Schema(
                                    fields=[geminidataanalytics.Field(name="cost", type_="FLOAT")]
                                ),
                            )
                            for p, d, t in tables
                        ]
                    )
                ),
                example_queries=[
                    geminidataanalytics.ExampleQuery(
                        natural_language_question="Total?",
                        sql_query="SELECT SUM(x) FROM `dev.d.t`",
                        parameters=[geminidataanalytics.QueryParameter(name="n", data_type="INT64")],
                    )
                ],
                glossary_terms=[geminidataanalytics.GlossaryTerm(display_name="GMV", labels=["finance"])],
                options=geminidataanalytics.ConversationOptions(
                    model=geminidataanalytics.ConversationOptions.Model.LATEST_GA_MODEL,
                    datasource=geminidataanalytics.DatasourceOptions(big_query_max_billed_bytes=5 * 10**9),
                ),
            ),
        ),
    )


def test_agent_to_config_exports_published_context_only():
    config = agent_to_config(live_agent(), "sales_agent", "dev")

    assert config == {
        "agent_id": "sales_agent",
        "location": "us",
        "display_name": "Sales Agent",
        "description": "Sales questions",
        "labels": {"env": "dev"},
        "source_project_id": "dev",
        "system_instruction": "Be precise.",
        "table_references": [{"project_id": "dev", "dataset_id": "d", "table_id": "t"}],
        "example_queries": [{
            "natural_language_question": "Total?",
            "sql_query": "SELECT SUM(x) FROM `dev.d.t`",
            "parameters": [{"name": "n", "description": "", "data_type": "INT64"}],
        }],
        "glossary_terms": [{"display_name": "GMV", "description": "", "labels": ["finance"]}],
        "options": {"python_enabled": False, "model": "LATEST_GA_MODEL", "max_billed_bytes": 5 * 10**9},
    }


def test_exported_config_round_trips_through_deployer():
    config = agent_to_config(live_agent(), "sales_agent", "dev")
    # Even when the CLI default location is "global", the exported "us" location is preserved.
    spec = specs.parse_agent_config(config, "out.json", "prd", "global")
    ctx = spec.data_agent.data_analytics_agent.published_context

    assert spec.location == "us"
    assert spec.data_agent.name == "projects/prd/locations/us/dataAgents/sales_agent"
    assert dict(spec.data_agent.labels) == {"env": "dev"}
    assert ctx.datasource_references.bq.table_references[0].project_id == "prd"
    assert ctx.example_queries[0].sql_query == "SELECT SUM(x) FROM `prd.d.t`"
    assert ctx.glossary_terms[0].display_name == "GMV"
    assert ctx.options.model == geminidataanalytics.ConversationOptions.Model.LATEST_GA_MODEL
    assert ctx.options.datasource.big_query_max_billed_bytes == 5 * 10**9


@pytest.mark.parametrize("datasource", [None, geminidataanalytics.DatasourceOptions(),
                                        geminidataanalytics.DatasourceOptions(big_query_max_billed_bytes=0)])
def test_no_byte_limit_exports_as_zero(datasource):
    agent = live_agent()
    agent.data_analytics_agent.published_context.options.datasource = datasource
    assert agent_to_config(agent, "sales_agent", "dev")["options"]["max_billed_bytes"] == 0


def test_unpublished_agent_rejected():
    with pytest.raises(ExportError, match="Publish the agent"):
        agent_to_config(live_agent(tables=()), "sales_agent", "dev")


@pytest.mark.parametrize("display_name, expected", [
    ("Sales Agent", "sales_agent"),
    ("  FinOps / Billing (v2) ", "finops_billing_v2"),
    ("2026 forecast", "agent_2026_forecast"),
    ("", "agent_123"),
    ("!!!", "agent_123"),
    ("x" * 80, "x" * 63),
])
def test_default_agent_id(display_name, expected):
    agent_id = default_agent_id(display_name, fallback="agent_123")
    assert agent_id == expected
    assert specs.AGENT_ID_RE.match(agent_id)


@pytest.mark.parametrize("source", [
    "projects/p1/locations/global/dataAgents/agent_9c-1",
    "https://geminidataanalytics.googleapis.com/v1/a2a/projects/p1/locations/global/dataAgents/agent_9c-1",
    "https://geminidataanalytics.googleapis.com/v1beta/a2a/projects/p1/locations/global/dataAgents/agent_9c-1/v1/card",
])
def test_parse_source_accepts_name_or_url(source):
    assert parse_source(source) == ("p1", "global", "agent_9c-1")


def test_parse_source_rejects_garbage():
    with pytest.raises(ValueError):
        parse_source("not-a-resource")


# ---------------------------------------------------------------- main()


SOURCE = "projects/dev/locations/us/dataAgents/agent_123"


class FakeClient:
    def __init__(self, agent=None, error=None):
        self.agent, self.error, self.requested = agent, error, []

    def get_data_agent(self, name):
        self.requested.append(name)
        if self.error:
            raise self.error
        return self.agent


@pytest.fixture
def export(monkeypatch, capsys):
    """Run export_agent.main() against a fake API; returns (exit_code, stdout, stderr, client)."""
    state = {"client": FakeClient(live_agent()), "locations": []}

    def fake_client(credentials, location):
        state["locations"].append(location)
        return state["client"]

    monkeypatch.setattr(export_agent, "get_credentials", lambda: object())
    monkeypatch.setattr(export_agent, "data_agent_client", fake_client)

    def run(*argv, client=None):
        if client is not None:
            state["client"] = client
        try:
            export_agent.main(list(argv))
            code = 0
        except SystemExit as e:
            code = e.code
        out, err = capsys.readouterr()
        return code, out, err, state

    return run


def test_main_writes_a_deployable_config(export, tmp_path):
    out_path = tmp_path / "nested" / "dir" / "sales.json"  # parent directories are created
    code, out, _, state = export("--source", SOURCE, "--out", str(out_path))

    assert code == 0
    assert state["locations"] == ["us"]  # regional endpoint for the source agent
    assert state["client"].requested == [SOURCE]
    config = json.loads(out_path.read_text())
    assert config["agent_id"] == "sales_agent"  # derived from the display name "Sales Agent"
    assert config["location"] == "us"
    assert config["source_project_id"] == "dev"
    assert "deploys as:      sales_agent (location: us)" in out
    specs.parse_agent_config(config, str(out_path), "prd")  # the written file deploys as-is


def test_main_accepts_a_card_url_and_agent_id_override(export, tmp_path):
    out_path = tmp_path / "x.json"
    url = "https://geminidataanalytics.us.rep.googleapis.com/v1/a2a/projects/dev/locations/US/dataAgents/agent_123/v1/card"
    code, _, _, state = export("--source", url, "--out", str(out_path), "--agent_id", "custom_id")
    assert code == 0
    assert state["client"].requested == [SOURCE]  # location normalized to lower case
    assert json.loads(out_path.read_text())["agent_id"] == "custom_id"


def test_main_rejects_a_bad_source_with_exit_2(export, tmp_path):
    code, _, err, state = export("--source", "not-a-resource", "--out", str(tmp_path / "x.json"))
    assert code == 2
    assert "must contain projects/" in err
    assert state["locations"] == []  # never called the API
    assert not (tmp_path / "x.json").exists()


def test_main_unpublished_agent_exits_1_without_writing(export, tmp_path):
    out_path = tmp_path / "x.json"
    code, _, err, _ = export("--source", SOURCE, "--out", str(out_path), client=FakeClient(live_agent(tables=())))
    assert code == 1
    assert "not deployable" in err and "Publish the agent" in err
    assert not out_path.exists()


def test_main_invalid_agent_id_override_exits_1_without_writing(export, tmp_path):
    out_path = tmp_path / "x.json"
    code, _, err, _ = export("--source", SOURCE, "--out", str(out_path), "--agent_id", "Not Valid")
    assert code == 1
    assert "not deployable" in err
    assert not out_path.exists()


def test_main_api_error_exits_1(export, tmp_path):
    client = FakeClient(error=exceptions.NotFound("agent not found"))
    code, _, err, _ = export("--source", SOURCE, "--out", str(tmp_path / "x.json"), client=client)
    assert code == 1
    assert "Fetching Data Agent failed" in err


def test_main_auth_error_exits_1(export, monkeypatch, tmp_path):
    def no_credentials():
        raise AuthError("No Google Cloud credentials found.")

    monkeypatch.setattr(export_agent, "get_credentials", no_credentials)
    code, _, err, _ = export("--source", SOURCE, "--out", str(tmp_path / "x.json"))
    assert code == 1
    assert "No Google Cloud credentials found." in err
