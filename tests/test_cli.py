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

"""End-to-end tests for `python -m deployer` (deployer.cli.main), fully offline.

Google Cloud is replaced by:
  * a fake DataAgentServiceClient (one per location, like the real CLI creates),
  * fake credentials,
  * an httpx.MockTransport serving Agent Registry and the A2A card endpoint.
"""

import json
import textwrap

import httpx
import pytest
from google.api_core import exceptions
from google.cloud import geminidataanalytics

from deployer import auth, cli

PROJECT = "prd-proj"
REGISTRY_HOST = "agentregistry.googleapis.com"


class FakeOp:
    def result(self):
        return None


class FakeDataAgentClient:
    """In-memory stand-in for DataAgentServiceClient, shared by all locations."""

    def __init__(self):
        self.live = {}  # resource name -> DataAgent
        self.failing = set()  # agent IDs whose GET raises PermissionDenied
        self.calls = []

    def get_data_agent(self, name):
        self.calls.append(("get", name.rsplit("/", 1)[-1]))
        if name.rsplit("/", 1)[-1] in self.failing:
            raise exceptions.PermissionDenied("caller lacks geminidataanalytics.dataAgents.get")
        if name not in self.live:
            raise exceptions.NotFound("not found")
        return self.live[name]

    def create_data_agent(self, request):
        self.calls.append(("create", request.data_agent_id))
        self.live[request.data_agent.name] = request.data_agent
        return FakeOp()

    def update_data_agent(self, request):
        self.calls.append(("update", request.data_agent.name.rsplit("/", 1)[-1]))
        self.live[request.data_agent.name] = request.data_agent
        return FakeOp()

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != "get"]


class Env:
    """A temp repo layout plus fakes for every external dependency of cli.main()."""

    def __init__(self, tmp_path, monkeypatch, capsys):
        self.config_dir = tmp_path / "agent_configs"
        self.low_code_dir = tmp_path / "low_code_agents"
        self.config_dir.mkdir()
        self.low_code_dir.mkdir()
        self.capsys = capsys
        self.client = FakeDataAgentClient()
        self.client_locations = []  # one entry per data_agent_client() call
        self.services = {}  # registry location -> list of services
        self.registry_write_status = 200
        self.requests = []
        self.credentials_requested = False

        def fake_credentials():
            self.credentials_requested = True
            return type("Creds", (), {"token": "test-token"})()

        def fake_data_agent_client(credentials, location):
            self.client_locations.append(location)
            return self.client

        real_client = httpx.Client
        monkeypatch.setattr(cli, "get_credentials", fake_credentials)
        monkeypatch.setattr(cli, "data_agent_client", fake_data_agent_client)
        monkeypatch.setattr(
            cli.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(self._handle), **kw)
        )

    # ---- external HTTP (Agent Registry + A2A card endpoint)

    def _handle(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/v1/card"):
            return httpx.Response(200, json={})
        if request.method == "GET":
            location = request.url.path.split("/locations/")[1].split("/")[0]
            return httpx.Response(200, json={"services": self.services.get(location, [])})
        if self.registry_write_status >= 400:
            return httpx.Response(self.registry_write_status, text="registry unavailable")
        return httpx.Response(200, json={"name": "projects/p/locations/global/operations/1", "done": True})

    @property
    def registry_requests(self):
        return [r for r in self.requests if r.url.host == REGISTRY_HOST]

    @property
    def registry_writes(self):
        return [(r.method, r.url.path) for r in self.registry_requests if r.method in ("POST", "PATCH")]

    # ---- repo content

    def config(self, agent_id, **overrides):
        data = {
            "agent_id": agent_id,
            "display_name": agent_id,
            "table_references": [{"project_id": "p", "dataset_id": "d", "table_id": "t"}],
            **overrides,
        }
        (self.config_dir / f"{agent_id}.json").write_text(json.dumps(data))

    def low_code(self, agent_id):
        (self.low_code_dir / f"{agent_id}.py").write_text(textwrap.dedent(f"""
            from google.cloud import geminidataanalytics as g
            AGENT_ID = "{agent_id}"
            def build_agent(project_id, location):
                return g.DataAgent(
                    display_name="{agent_id}",
                    data_analytics_agent=g.DataAnalyticsAgent(published_context=g.Context(
                        datasource_references=g.DatasourceReferences(bq=g.BigQueryTableReferences(
                            table_references=[g.BigQueryTableReference(project_id="p", dataset_id="d", table_id="t")]
                        ))
                    )),
                )
        """))

    def deploy_current_state(self, register=True):
        """Make the live project match the repo, as if a previous run had succeeded."""
        for spec in cli.load_all(str(self.config_dir), str(self.low_code_dir), PROJECT, "global"):
            live = geminidataanalytics.DataAgent.deserialize(geminidataanalytics.DataAgent.serialize(spec.data_agent))
            self.client.live[spec.data_agent.name] = live
            if register and spec.register:
                self.services.setdefault("global", []).append({
                    "name": f"projects/{PROJECT}/locations/global/services/{spec.agent_id.replace('_', '-')}",
                    "agentSpec": {"content": {"url": f"https://x/v1/a2a/projects/{PROJECT}/locations/{spec.location}/dataAgents/{spec.agent_id}"}},
                })

    def edit_live(self, agent_id, **fields):
        for name, agent in self.client.live.items():
            if name.endswith(f"/dataAgents/{agent_id}"):
                for key, value in fields.items():
                    setattr(agent, key, value)

    # ---- running the CLI

    def run(self, *args):
        argv = [f"--project_id={PROJECT}", f"--config_dir={self.config_dir}", f"--low_code_dir={self.low_code_dir}", *args]
        try:
            cli.main(argv)
            code = 0
        except SystemExit as e:
            code = e.code
        out, err = self.capsys.readouterr()
        return code, out, err


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    return Env(tmp_path, monkeypatch, capsys)


def line_for(out, agent_id):
    return next(line for line in out.splitlines() if f"] {agent_id} " in line)


# ---------------------------------------------------------------- exit codes


def test_no_definitions_exits_0_without_authenticating(env):
    code, out, _ = env.run()
    assert code == 0
    assert "0 agent definition(s) found" in out
    assert not env.credentials_requested


def test_invalid_definition_exits_2_before_authenticating_or_writing(env):
    env.config("good_agent")
    (env.config_dir / "bad_agent.json").write_text(json.dumps({"agent_id": "bad_agent"}))  # no tables
    code, _, err = env.run()
    assert code == 2
    assert "Invalid agent definition" in err and "at least one table" in err
    assert not env.credentials_requested
    assert env.client.calls == []


def test_duplicate_agent_ids_exit_2(env):
    env.config("dup_agent")
    env.low_code("dup_agent")
    code, _, err = env.run()
    assert code == 2
    assert "Duplicate agent ID 'dup_agent'" in err


def test_auth_failure_exits_1(env, monkeypatch):
    env.config("my_agent")

    def no_credentials():
        raise auth.AuthError("No Google Cloud credentials found. Run: gcloud auth application-default login")

    monkeypatch.setattr(cli, "get_credentials", no_credentials)
    code, _, err = env.run()
    assert code == 1
    assert "gcloud auth application-default login" in err
    assert env.client.calls == []


# ---------------------------------------------------------------- create / skip / modify


def test_new_agent_is_created_and_registered(env):
    env.config("new_agent")
    code, out, _ = env.run()
    assert code == 0
    assert line_for(out, "new_agent").startswith("[CREATE] new_agent [global]")
    assert env.client.writes == [("create", "new_agent")]
    assert env.registry_writes == [("POST", f"/v1/projects/{PROJECT}/locations/global/services")]
    assert "1 created, 0 modified, 0 skipped, 0 failed" in out


def test_unchanged_agents_are_skipped_without_any_writes(env):
    env.config("stable_agent")
    env.low_code("stable_low_code")
    env.deploy_current_state()
    code, out, _ = env.run()
    assert code == 0
    assert line_for(out, "stable_agent").startswith("[SKIP]")
    assert line_for(out, "stable_low_code").startswith("[SKIP]")
    assert env.client.writes == []
    assert env.registry_writes == []
    assert "0 created, 0 modified, 2 skipped, 0 failed" in out


def test_drifted_agent_is_updated_and_registry_entry_refreshed(env):
    env.config("drifted_agent")
    env.deploy_current_state()
    env.edit_live("drifted_agent", description="edited by hand in production")
    code, out, _ = env.run()
    assert code == 0
    assert "[MODIFY] drifted_agent" in out and "changed: description" in out
    assert env.client.writes == [("update", "drifted_agent")]
    assert [m for m, _ in env.registry_writes] == ["PATCH"]


def test_missing_registry_entry_turns_skip_into_modify(env):
    env.config("unregistered_agent")
    env.deploy_current_state(register=False)
    code, out, _ = env.run()
    assert code == 0
    line = line_for(out, "unregistered_agent")
    assert line.startswith("[MODIFY]")
    assert "agent_registry (missing in ['global'])" in line
    assert env.client.writes == []  # the Data Agent itself is already up to date
    assert env.registry_writes == [("POST", f"/v1/projects/{PROJECT}/locations/global/services")]


def test_low_code_agents_are_never_registered(env):
    env.low_code("low_code_agent")
    code, out, _ = env.run()
    assert code == 0
    assert line_for(out, "low_code_agent").startswith("[CREATE]")
    assert env.registry_requests == []


# ---------------------------------------------------------------- flags


def test_dry_run_reports_plan_without_any_writes(env):
    env.config("new_agent")
    env.config("drifted_agent")
    env.deploy_current_state()
    del env.client.live[f"projects/{PROJECT}/locations/global/dataAgents/new_agent"]
    env.edit_live("drifted_agent", display_name="renamed by hand")
    code, out, _ = env.run("--dry-run")
    assert code == 0
    assert line_for(out, "new_agent").startswith("[PLAN] [CREATE]")
    assert line_for(out, "drifted_agent").startswith("[PLAN] [MODIFY]")
    assert env.client.writes == []
    assert env.registry_writes == []
    assert "[dry run: nothing was changed]" in out


def test_skip_registry_makes_no_registry_calls(env):
    env.config("new_agent")
    code, out, _ = env.run("--skip-registry")
    assert code == 0
    assert line_for(out, "new_agent").startswith("[CREATE]")
    assert env.registry_requests == []


def test_registry_locations_flag_writes_each_location(env):
    env.config("new_agent")
    code, _, _ = env.run("--registry_locations", "global", "US-Central1")
    assert code == 0
    assert env.registry_writes == [
        ("POST", f"/v1/projects/{PROJECT}/locations/global/services"),
        ("POST", f"/v1/projects/{PROJECT}/locations/us-central1/services"),  # lower-cased
    ]


# ---------------------------------------------------------------- error isolation


def test_one_failing_agent_does_not_stop_the_others(env):
    env.config("a_broken_agent")
    env.config("b_new_agent")
    env.client.failing.add("a_broken_agent")
    code, out, _ = env.run()
    assert code == 1
    broken = out.split("[ERROR] a_broken_agent", 1)[1]
    assert "PermissionDenied" in broken.splitlines()[1]
    assert line_for(out, "b_new_agent").startswith("[CREATE]")
    assert env.client.writes == [("create", "b_new_agent")]
    assert "1 created, 0 modified, 0 skipped, 1 failed" in out


def test_registry_failure_after_a_successful_create_is_reported_as_error(env):
    """The Data Agent is created, but the run fails so the missing registry entry isn't missed."""
    env.config("new_agent")
    env.registry_write_status = 500
    code, out, _ = env.run()
    assert code == 1
    assert "[ERROR] new_agent" in out and "registry unavailable" in out
    assert env.client.writes == [("create", "new_agent")]


# ---------------------------------------------------------------- locations


def test_one_client_per_location_and_location_shown_per_agent(env):
    env.config("global_one")
    env.config("global_two")
    env.config("us_agent", location="US")
    code, out, _ = env.run("--skip-registry")
    assert code == 0
    assert sorted(env.client_locations) == ["global", "us"]  # created once each, then reused
    assert line_for(out, "us_agent").startswith("[CREATE] us_agent [us]")
    assert ("create", "us_agent") in env.client.writes
    assert f"projects/{PROJECT}/locations/us/dataAgents/us_agent" in env.client.live
