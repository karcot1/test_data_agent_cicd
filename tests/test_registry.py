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

import httpx
import pytest

from deployer import registry


def svc(name, url=""):
    return {"name": name, "agentSpec": {"content": {"url": url}}}


def test_service_id_and_display_name_truncated():
    long_id = "x" * 70
    assert len(registry.service_id_for(long_id)) <= 63
    assert len(registry.display_name_for(long_id)) <= 63
    assert registry.service_id_for("a_b_c") == "a-b-c"


def card_url(project, agent_id, location="global"):
    return f"https://geminidataanalytics.googleapis.com/v1/a2a/projects/{project}/locations/{location}/dataAgents/{agent_id}"


def test_find_service_by_card_url():
    services = [
        svc("projects/p/locations/global/services/other", card_url("p", "other")),
        svc("projects/p/locations/global/services/auto-generated-123", card_url("p", "my_agent")),
    ]
    assert registry.find_service(services, "my_agent", "p")["name"].endswith("auto-generated-123")


def test_find_service_by_card_url_in_any_location_and_with_card_suffix():
    assert registry.find_service([svc("s1", card_url("p", "my_agent", "us"))], "my_agent", "p")
    assert registry.find_service([svc("s1", card_url("p", "my_agent") + "/v1/card")], "my_agent", "p")


def test_find_service_by_service_id():
    services = [svc("projects/p/locations/global/services/my-agent")]
    assert registry.find_service(services, "my_agent", "p") is not None


@pytest.mark.parametrize("url", [
    card_url("p", "my_agent_v2"),        # longer agent ID
    card_url("p", "my_agent-eu"),        # longer agent ID with a hyphen
    card_url("other-proj", "my_agent"),  # same agent ID in another project
    card_url("p-2", "my_agent"),         # project ID that starts with ours
])
def test_find_service_rejects_near_misses(url):
    assert registry.find_service([svc("projects/p/locations/global/services/x", url)], "my_agent", "p") is None


def test_missing_locations_uses_cached_lists(mock_http):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/locations/global/" in request.url.path:
            return httpx.Response(200, json={"services": [svc("projects/p/locations/global/services/my-agent")]})
        return httpx.Response(200, json={})

    reg = registry.AgentRegistry(mock_http(handler), "p", registry_locations=["global", "us-central1"])

    assert reg.missing_locations("my_agent") == ["us-central1"]
    assert reg.missing_locations("other_agent") == ["global", "us-central1"]
    assert len(calls) == 2  # one list call per location, then cached


def test_default_registry_location_is_global_only(mock_http):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    reg = registry.AgentRegistry(mock_http(handler), "p")
    assert reg.registry_locations == ("global",)
    assert reg.missing_locations("my_agent") == ["global"]
    assert calls == ["/v1/projects/p/locations/global/services"]


def test_registry_locations_deduplicated(mock_http):
    reg = registry.AgentRegistry(mock_http(), "p", registry_locations=["global", "global", "us-central1"])
    assert reg.registry_locations == ("global", "us-central1")


def test_services_follows_pagination(mock_http):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("pageToken") == "page2":
            return httpx.Response(200, json={"services": [svc("projects/p/locations/global/services/my-agent")]})
        return httpx.Response(200, json={"services": [svc("projects/p/locations/global/services/other")], "nextPageToken": "page2"})

    reg = registry.AgentRegistry(mock_http(handler), "p")
    assert len(reg.services("global")) == 2
    assert reg.missing_locations("my_agent") == []


def test_services_raises_on_api_error(mock_http):
    reg = registry.AgentRegistry(mock_http(lambda r: httpx.Response(403)), "p")
    with pytest.raises(httpx.HTTPStatusError):
        reg.missing_locations("my_agent")


# ---------------------------------------------------------------- sync


OP_NAME = "projects/p/locations/global/operations/1"


def registry_api(services=(), write_response=None, op_polls=()):
    """Handler for a fake Agent Registry plus the Gemini Data Analytics card endpoint.

    services: listed by GET .../services. write_response: returned by POST/PATCH
    (default: a finished operation). op_polls: responses for successive operation GETs.
    """
    requests = []
    polls = list(op_polls)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/v1/card"):
            return httpx.Response(200, json={"preferredTransport": "HTTP_JSON"})
        if request.method == "GET" and request.url.path.endswith(f"/{OP_NAME}"):
            return httpx.Response(200, json=polls.pop(0))
        if request.method == "GET":
            return httpx.Response(200, json={"services": list(services)})
        if write_response is not None:
            return write_response
        return httpx.Response(200, json={"name": OP_NAME, "done": True})

    return handler, requests


def test_sync_creates_service_from_regional_card_and_normalizes_it(mock_http):
    handler, requests = registry_api()
    reg = registry.AgentRegistry(mock_http(handler), "p", location="us")
    reg.sync("my_agent", "My description")

    card_request = requests[0]
    assert card_request.url.host == "geminidataanalytics.us.rep.googleapis.com"
    post = next(r for r in requests if r.method == "POST")
    assert post.url.params["serviceId"] == "my-agent"
    payload = json.loads(post.content)
    card = payload["agentSpec"]["content"]
    assert card["preferredTransport"] == "HTTP+JSON"
    assert card["description"] == "My description"
    assert card["url"].endswith("/locations/us/dataAgents/my_agent")
    assert payload["displayName"] == "my_agent [BQCA Agent]"
    assert not any(r.method == "PATCH" for r in requests)


def test_sync_patches_an_existing_service_instead_of_creating_a_duplicate(mock_http):
    existing = svc("projects/p/locations/global/services/auto-generated-123", card_url("p", "my_agent"))
    handler, requests = registry_api(services=[existing])
    registry.AgentRegistry(mock_http(handler), "p").sync("my_agent", "d")

    assert not any(r.method == "POST" for r in requests)
    (patch,) = [r for r in requests if r.method == "PATCH"]
    assert patch.url.path == "/v1/projects/p/locations/global/services/auto-generated-123"
    assert patch.url.params["updateMask"] == "agentSpec,displayName,description"
    assert json.loads(patch.content)["agentSpec"]["type"] == "A2A_AGENT_CARD"


def test_sync_writes_every_configured_location(mock_http):
    handler, requests = registry_api()
    registry.AgentRegistry(mock_http(handler), "p", registry_locations=["global", "us-central1"]).sync("my_agent")
    posts = [r.url.path for r in requests if r.method == "POST"]
    assert posts == ["/v1/projects/p/locations/global/services", "/v1/projects/p/locations/us-central1/services"]


@pytest.mark.parametrize("status", [400, 409, 500])
def test_sync_raises_on_write_error(mock_http, status):
    handler, _ = registry_api(write_response=httpx.Response(status, text="boom"))
    with pytest.raises(RuntimeError, match=rf"Agent Registry API error \({status}\) in 'global': boom"):
        registry.AgentRegistry(mock_http(handler), "p").sync("my_agent")


def test_sync_raises_when_card_cannot_be_fetched(mock_http):
    reg = registry.AgentRegistry(mock_http(lambda r: httpx.Response(404)), "p")
    with pytest.raises(httpx.HTTPStatusError):
        reg.sync("my_agent")


# ---------------------------------------------------------------- operations


def test_wait_polls_until_done(mock_http, monkeypatch):
    monkeypatch.setattr(registry, "OPERATION_POLL_SECONDS", 0)
    handler, requests = registry_api(op_polls=[{"name": OP_NAME, "done": False}, {"name": OP_NAME, "done": True}])
    reg = registry.AgentRegistry(mock_http(handler), "p")
    reg._wait({"name": OP_NAME, "done": False}, "global")
    assert [r.url.path for r in requests] == [f"/v1/{OP_NAME}", f"/v1/{OP_NAME}"]


def test_wait_raises_error_reported_while_polling(mock_http, monkeypatch):
    monkeypatch.setattr(registry, "OPERATION_POLL_SECONDS", 0)
    handler, _ = registry_api(op_polls=[{"name": OP_NAME, "done": True, "error": {"code": 6, "message": "exists"}}])
    with pytest.raises(RuntimeError, match="operation failed in 'global'"):
        registry.AgentRegistry(mock_http(handler), "p")._wait({"name": OP_NAME, "done": False}, "global")


def test_wait_returns_immediately_for_non_operation_responses(mock_http):
    handler, requests = registry_api()
    registry.AgentRegistry(mock_http(handler), "p")._wait({"name": "projects/p/locations/global/services/x"}, "global")
    assert requests == []


def test_wait_times_out(mock_http, monkeypatch):
    monkeypatch.setattr(registry, "OPERATION_TIMEOUT_SECONDS", 0)
    op = {"name": OP_NAME, "done": False}
    reg = registry.AgentRegistry(mock_http(lambda r: httpx.Response(200, json=op)), "p")
    with pytest.raises(TimeoutError):
        reg._wait(op, "global")


def test_wait_raises_operation_error(mock_http):
    op = {"name": OP_NAME, "done": True, "error": {"code": 6}}
    with pytest.raises(RuntimeError, match="operation failed"):
        registry.AgentRegistry(mock_http(), "p")._wait(op, "global")
