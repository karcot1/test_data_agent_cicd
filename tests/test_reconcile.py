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
from google.api_core import exceptions
from google.cloud import geminidataanalytics
from google.protobuf import timestamp_pb2

from deployer import reconcile as rc
from deployer.specs import AgentSpec

NAME = "projects/p/locations/global/dataAgents/a"


def make_agent(
    instruction="inst",
    tables=(("p", "d", "t"),),
    examples=(("q", "SELECT 1"),),
    display_name="a",
    description="desc",
    name=NAME,
    glossary=(("GMV", "Gross merchandise value"),),
    python_enabled=False,
):
    return geminidataanalytics.DataAgent(
        name=name,
        display_name=display_name,
        description=description,
        data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
            published_context=geminidataanalytics.Context(
                system_instruction=instruction,
                datasource_references=geminidataanalytics.DatasourceReferences(
                    bq=geminidataanalytics.BigQueryTableReferences(
                        table_references=[
                            geminidataanalytics.BigQueryTableReference(project_id=p, dataset_id=d, table_id=t)
                            for p, d, t in tables
                        ]
                    )
                ),
                example_queries=[
                    geminidataanalytics.ExampleQuery(natural_language_question=q, sql_query=s)
                    for q, s in examples
                ],
                glossary_terms=[
                    geminidataanalytics.GlossaryTerm(display_name=n, description=d) for n, d in glossary
                ],
                options=geminidataanalytics.ConversationOptions(
                    analysis=geminidataanalytics.AnalysisOptions(
                        python=geminidataanalytics.AnalysisOptions.Python(enabled=python_enabled)
                    )
                ),
            )
        ),
    )


# ---------------------------------------------------------------- diff


def test_identical_has_no_diff():
    assert rc.diff_data_agent(make_agent(), make_agent()) == []


def test_whitespace_in_instruction_ignored():
    assert rc.diff_data_agent(make_agent(instruction="\ninst\n"), make_agent()) == []


@pytest.mark.parametrize("kwargs, field", [
    ({"instruction": "other"}, "system_instruction"),
    ({"tables": (("p", "d", "t2"),)}, "table_references"),
    ({"tables": ()}, "table_references"),
    ({"examples": (("q", "SELECT 2"),)}, "example_queries"),
    ({"examples": ()}, "example_queries"),
    ({"display_name": "b"}, "display_name"),
    ({"description": "new"}, "description"),
    ({"glossary": (("GMV", "changed"),)}, "glossary_terms"),
    ({"glossary": ()}, "glossary_terms"),
    ({"python_enabled": True}, "python_enabled"),
])
def test_each_field_detected(kwargs, field):
    assert rc.diff_data_agent(make_agent(), make_agent(**kwargs)) == [field]


def test_unset_display_name_and_description_ignored():
    desired = make_agent(display_name="", description="")
    assert rc.diff_data_agent(make_agent(display_name="live", description="live"), desired) == []


def test_soft_deleted_detected():
    existing = make_agent()
    existing.delete_time = timestamp_pb2.Timestamp(seconds=1)
    assert "delete_time (soft-deleted)" in rc.diff_data_agent(existing, make_agent())


def test_update_mask_only_includes_set_fields():
    assert list(rc.update_mask_for(make_agent()).paths) == ["display_name", "description", "data_analytics_agent"]
    assert list(rc.update_mask_for(make_agent(display_name="", description="")).paths) == ["data_analytics_agent"]


# ---------------------------------------------------------------- reconcile


class FakeOp:
    def result(self):
        return None


class FakeClient:
    def __init__(self, existing=None, create_raises=None):
        self.existing = existing
        self.create_raises = create_raises
        self.calls = []

    def get_data_agent(self, name):
        self.calls.append("get")
        if self.existing is None:
            raise exceptions.NotFound("nope")
        return self.existing

    def create_data_agent(self, request):
        self.calls.append("create")
        self.create_request = request
        if self.create_raises:
            raise self.create_raises
        return FakeOp()

    def update_data_agent(self, request):
        self.calls.append("update")
        self.update_request = request
        return FakeOp()


def spec(agent=None):
    return AgentSpec("a", "a.json", agent or make_agent())


def test_reconcile_creates_when_missing():
    client = FakeClient()
    result = rc.reconcile(client, spec())
    assert result.action == rc.CREATE
    assert client.calls == ["get", "create"]
    assert client.create_request.parent == "projects/p/locations/global"
    assert client.create_request.data_agent_id == "a"


def test_reconcile_soft_deleted_falls_back_to_update():
    client = FakeClient(create_raises=exceptions.AlreadyExists("soft deleted"))
    assert rc.reconcile(client, spec()).action == rc.CREATE
    assert client.calls == ["get", "create", "update"]


def test_reconcile_skips_unchanged():
    client = FakeClient(existing=make_agent())
    assert rc.reconcile(client, spec()).action == rc.SKIP
    assert client.calls == ["get"]


def test_reconcile_updates_changed():
    client = FakeClient(existing=make_agent(instruction="old"))
    result = rc.reconcile(client, spec())
    assert result.action == rc.MODIFY
    assert result.changed_fields == ["system_instruction"]
    assert client.calls == ["get", "update"]


@pytest.mark.parametrize("existing", [None, make_agent(instruction="old")])
def test_dry_run_never_writes(existing):
    client = FakeClient(existing=existing)
    result = rc.reconcile(client, spec(), dry_run=True)
    assert result.action in (rc.CREATE, rc.MODIFY)
    assert client.calls == ["get"]
