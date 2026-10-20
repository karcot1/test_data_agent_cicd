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

import os

import pytest
from google.api_core import exceptions
from google.cloud import geminidataanalytics
from google.protobuf import timestamp_pb2

from deployer import reconcile as rc
from deployer import specs
from deployer.specs import AgentSpec

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "projects/p/locations/global/dataAgents/a"
ALL_PATHS = [
    "display_name",
    "description",
    "labels",
    "data_analytics_agent.published_context",
    "data_analytics_agent.staging_context",
]


def make_context(
    instruction="inst",
    tables=(("p", "d", "t"),),
    examples=(("q", "SELECT 1"),),
    glossary=(("GMV", "Gross merchandise value"),),
    python_enabled=False,
    model="MODEL_UNSPECIFIED",
    max_billed_bytes=None,
):
    return geminidataanalytics.Context(
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
            ),
            model=geminidataanalytics.ConversationOptions.Model[model],
            datasource=(
                geminidataanalytics.DatasourceOptions(big_query_max_billed_bytes=max_billed_bytes)
                if max_billed_bytes is not None
                else None
            ),
        ),
    )


def make_agent(display_name="a", description="desc", labels=None, name=NAME, staging=None, **context_kwargs):
    """An agent shaped like what the deployer builds: staging_context mirrors published_context."""
    published = make_context(**context_kwargs)
    return geminidataanalytics.DataAgent(
        name=name,
        display_name=display_name,
        description=description,
        labels=labels or {},
        data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
            published_context=published,
            staging_context=published if staging is None else staging,
        ),
    )


def as_returned_by_api(agent):
    """Copy of `agent` with the server-populated fields a GetDataAgent response carries."""
    live = geminidataanalytics.DataAgent.deserialize(geminidataanalytics.DataAgent.serialize(agent))
    live.create_time = timestamp_pb2.Timestamp(seconds=1)
    live.update_time = timestamp_pb2.Timestamp(seconds=2)
    live.data_analytics_agent.last_published_context = make_context(instruction="previous version")
    return live


def drift(mutate):
    """A live agent equal to make_agent() except for `mutate` applied to both contexts."""
    live = make_agent()
    for ctx in (live.data_analytics_agent.published_context, live.data_analytics_agent.staging_context):
        mutate(ctx)
    return live


# ---------------------------------------------------------------- diff: managed fields


def test_identical_has_no_diff():
    assert rc.diff_data_agent(make_agent(), make_agent()) == []


def test_whitespace_in_free_text_ignored():
    live = make_agent(
        instruction="\ninst\n", examples=((" q ", "SELECT 1\n"),), glossary=((" GMV", "Gross merchandise value "),)
    )
    assert rc.diff_data_agent(live, make_agent()) == []


@pytest.mark.parametrize("kwargs, fields", [
    ({"instruction": "other"}, ["system_instruction"]),
    ({"tables": (("p", "d", "t2"),)}, ["table_references"]),
    ({"tables": ()}, ["table_references"]),
    ({"examples": (("q", "SELECT 2"),)}, ["example_queries"]),
    ({"examples": ()}, ["example_queries"]),
    ({"glossary": (("GMV", "changed"),)}, ["glossary_terms"]),
    ({"glossary": ()}, ["glossary_terms"]),
    ({"python_enabled": True}, ["python_enabled"]),
    ({"model": "LATEST_GA_MODEL"}, ["model"]),
    ({"max_billed_bytes": 10**9}, ["max_billed_bytes"]),
])
def test_each_context_field_detected_in_published_and_staging(kwargs, fields):
    # The deployer keeps staging == published, so a context change shows up in both.
    assert rc.diff_data_agent(make_agent(), make_agent(**kwargs)) == fields + ["staging_context"]


@pytest.mark.parametrize("kwargs, field", [
    ({"display_name": "b"}, "display_name"),
    ({"description": "new"}, "description"),
    ({"labels": {"env": "prod"}}, "labels"),
])
def test_each_agent_field_detected(kwargs, field):
    assert rc.diff_data_agent(make_agent(), make_agent(**kwargs)) == [field]


# ---------------------------------------------------------------- diff: configs are the source of truth


@pytest.mark.parametrize("field", ["display_name", "description"])
def test_empty_display_name_and_description_are_enforced(field):
    """An empty value in the definition means the live value must be cleared too."""
    assert rc.diff_data_agent(make_agent(**{field: "set in the console"}), make_agent(**{field: ""})) == [field]


def test_console_labels_are_detected_and_removed():
    live = make_agent(labels={"owner": "finance-team"})
    assert rc.diff_data_agent(live, make_agent()) == ["labels"]
    assert "labels" in rc.update_mask_for(make_agent()).paths


def _schema_description(ctx):
    ctx.datasource_references.bq.table_references[0].schema = geminidataanalytics.Schema(description="curated")


def _column_description(ctx):
    ctx.datasource_references.bq.table_references[0].schema = geminidataanalytics.Schema(
        fields=[geminidataanalytics.Field(name="id", description="primary key")]
    )


def _schema_relationship(ctx):
    ctx.schema_relationships = [geminidataanalytics.Context.SchemaRelationship(confidence_score=0.9)]


def _user_function(ctx):
    ctx.user_functions = geminidataanalytics.UserFunctions(
        bq_routines=[geminidataanalytics.BigQueryRoutine(description="udf")]
    )


def _studio_source(ctx):
    ctx.datasource_references.studio = geminidataanalytics.StudioDatasourceReferences(
        studio_references=[geminidataanalytics.StudioDatasourceReference(datasource_id="ds")]
    )


@pytest.mark.parametrize("mutate, field", [
    (_schema_description, "table_schema"),
    (_column_description, "table_schema"),
    (_schema_relationship, "schema_relationships"),
    (_user_function, "user_functions"),
])
def test_drift_in_fields_the_config_format_cannot_express_is_detected(mutate, field):
    """Edits made by hand in a higher environment are reverted on the next deploy."""
    assert rc.diff_data_agent(drift(mutate), make_agent()) == [field, "staging_context"]


def test_switching_to_another_datasource_type_detected():
    # bq, studio and looker are alternatives, so a Studio source replaces the BigQuery tables.
    assert rc.diff_data_agent(drift(_studio_source), make_agent()) == [
        "table_references", "datasource_references", "staging_context",
    ]


@pytest.mark.parametrize("live_value", [0, "empty"])
def test_no_limit_forms_written_by_bigquery_studio_equal_unset(live_value):
    """Studio stores "no limit" as {} or 0; neither may cause a perpetual MODIFY."""
    live = make_agent(max_billed_bytes=0 if live_value == 0 else None)
    if live_value == "empty":
        for ctx in (live.data_analytics_agent.published_context, live.data_analytics_agent.staging_context):
            ctx.options.datasource = geminidataanalytics.DatasourceOptions()
    assert rc.diff_data_agent(live, make_agent()) == []


def test_removing_a_byte_limit_is_detected():
    live = make_agent(max_billed_bytes=10**9)
    assert rc.diff_data_agent(live, make_agent()) == ["max_billed_bytes", "staging_context"]


def test_staging_only_drift_detected():
    live = make_agent(staging=make_context(instruction="unpublished draft"))
    assert rc.diff_data_agent(live, make_agent()) == ["staging_context"]


def test_empty_live_staging_is_synced_once():
    """Agents deployed before staging sync existed report staging_context on their next run."""
    live = make_agent(staging=geminidataanalytics.Context())
    assert rc.diff_data_agent(live, make_agent()) == ["staging_context"]


def test_soft_deleted_detected():
    existing = make_agent()
    existing.delete_time = timestamp_pb2.Timestamp(seconds=1)
    assert "delete_time (soft-deleted)" in rc.diff_data_agent(existing, make_agent())


def test_server_managed_fields_ignored():
    live = as_returned_by_api(make_agent())
    live.kms_key = "projects/p/locations/global/keyRings/r/cryptoKeys/k"
    assert rc.diff_data_agent(live, make_agent()) == []


def test_unknown_protobuf_fields_in_context_ignored():
    """Fields added by a newer server version than the pinned SDK must not cause a perpetual MODIFY."""
    live = make_agent()
    # Tag 99999 (varint = 1) inside Context.options (field 3 on Context, length-delimited).
    unknown_tag = (99999 << 3) | 0
    tag_bytes = []
    v = unknown_tag
    while v >= 0x80:
        tag_bytes.append((v & 0x7F) | 0x80)
        v >>= 7
    tag_bytes.append(v)
    wire_Unknown = bytes(tag_bytes + [1])
    for ctx in (live.data_analytics_agent.published_context, live.data_analytics_agent.staging_context):
        pb = geminidataanalytics.Context.pb(ctx)
        pb.options.MergeFromString(wire_Unknown)
    assert rc.diff_data_agent(live, make_agent()) == []


def test_diff_does_not_mutate_inputs():
    live, desired = make_agent(instruction=" inst "), make_agent()
    rc.diff_data_agent(live, desired)
    assert live.data_analytics_agent.published_context.system_instruction == " inst "


# ---------------------------------------------------------------- idempotency


def _repo_specs():
    project = "prd-proj"
    loaded = specs.load_agent_configs(os.path.join(REPO_ROOT, "agent_configs"), project)
    loaded += specs.load_low_code_specs(os.path.join(REPO_ROOT, "low_code_agents"), project)
    return loaded


@pytest.mark.parametrize("spec", _repo_specs(), ids=lambda s: s.agent_id)
def test_deploying_twice_is_a_noop(spec):
    """After a deploy, reading the agent back must produce SKIP, never a perpetual MODIFY."""
    assert rc.diff_data_agent(as_returned_by_api(spec.data_agent), spec.data_agent) == []


@pytest.mark.parametrize("spec", _repo_specs(), ids=lambda s: s.agent_id)
def test_built_specs_keep_staging_equal_to_published(spec):
    analytics = spec.data_agent.data_analytics_agent
    assert analytics.staging_context == analytics.published_context


# ---------------------------------------------------------------- update mask


def test_update_mask_always_covers_every_owned_field():
    assert list(rc.update_mask_for(make_agent()).paths) == ALL_PATHS
    assert list(rc.update_mask_for(make_agent(display_name="", description="")).paths) == ALL_PATHS


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
        self.get_name = name
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
    desired = make_agent()
    client = FakeClient()
    result = rc.reconcile(client, spec(desired))
    assert result.action == rc.CREATE
    assert client.calls == ["get", "create"]
    assert client.get_name == NAME
    assert client.create_request.parent == "projects/p/locations/global"
    assert client.create_request.data_agent_id == "a"
    assert client.create_request.data_agent == desired


def test_reconcile_soft_deleted_falls_back_to_full_update():
    desired = make_agent()
    client = FakeClient(create_raises=exceptions.AlreadyExists("soft deleted"))
    assert rc.reconcile(client, spec(desired)).action == rc.CREATE
    assert client.calls == ["get", "create", "update"]
    assert client.update_request.data_agent == desired
    assert list(client.update_request.update_mask.paths) == ALL_PATHS


def test_reconcile_skips_unchanged():
    client = FakeClient(existing=as_returned_by_api(make_agent()))
    assert rc.reconcile(client, spec()).action == rc.SKIP
    assert client.calls == ["get"]


def test_reconcile_update_sends_the_full_desired_agent():
    desired = make_agent()
    client = FakeClient(existing=drift(_schema_relationship))
    result = rc.reconcile(client, spec(desired))
    assert result.action == rc.MODIFY
    assert result.changed_fields == ["schema_relationships", "staging_context"]
    assert client.calls == ["get", "update"]
    # The whole definition is written back, so the hand-made relationship is removed.
    assert client.update_request.data_agent == desired
    assert list(client.update_request.data_agent.data_analytics_agent.published_context.schema_relationships) == []
    assert list(client.update_request.update_mask.paths) == ALL_PATHS


@pytest.mark.parametrize("existing", [None, make_agent(instruction="old")])
def test_dry_run_never_writes(existing):
    client = FakeClient(existing=existing)
    result = rc.reconcile(client, spec(), dry_run=True)
    assert result.action in (rc.CREATE, rc.MODIFY)
    assert client.calls == ["get"]


def test_api_errors_other_than_not_found_propagate():
    class Denied(FakeClient):
        def get_data_agent(self, name):
            raise exceptions.PermissionDenied("no access")

    with pytest.raises(exceptions.PermissionDenied):
        rc.reconcile(Denied(), spec())
