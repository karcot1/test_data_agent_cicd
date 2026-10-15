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

import pytest
from google.cloud import geminidataanalytics

from deployer import specs
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
                    model=geminidataanalytics.ConversationOptions.Model.LATEST_GA_MODEL
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
        "options": {"python_enabled": False, "model": "LATEST_GA_MODEL"},
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
