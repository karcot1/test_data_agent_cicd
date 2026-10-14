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


def test_find_service_by_url_suffix():
    services = [
        svc("projects/p/locations/global/services/other", "https://x/dataAgents/other"),
        svc("projects/p/locations/global/services/auto-generated-123", "https://x/v1/a2a/projects/p/locations/global/dataAgents/my_agent"),
    ]
    assert registry.find_service(services, "my_agent")["name"].endswith("auto-generated-123")


def test_find_service_by_service_id():
    services = [svc("projects/p/locations/global/services/my-agent")]
    assert registry.find_service(services, "my_agent") is not None


def test_find_service_no_partial_url_match():
    services = [svc("projects/p/locations/global/services/x", "https://x/dataAgents/my_agent_v2")]
    assert registry.find_service(services, "my_agent") is None


def test_missing_locations_uses_cached_lists():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/locations/global/" in request.url.path:
            return httpx.Response(200, json={"services": [svc("projects/p/locations/global/services/my-agent")]})
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reg = registry.AgentRegistry(client, "p", registry_locations=["global", "us-central1"])

    assert reg.missing_locations("my_agent") == ["us-central1"]
    assert reg.missing_locations("other_agent") == ["global", "us-central1"]
    assert len(calls) == 2  # one list call per location, then cached


def test_default_registry_location_is_global_only():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    reg = registry.AgentRegistry(httpx.Client(transport=httpx.MockTransport(handler)), "p")
    assert reg.registry_locations == ("global",)
    assert reg.missing_locations("my_agent") == ["global"]
    assert calls == ["/v1/projects/p/locations/global/services"]


def test_registry_locations_deduplicated():
    reg = registry.AgentRegistry(httpx.Client(), "p", registry_locations=["global", "global", "us-central1"])
    assert reg.registry_locations == ("global", "us-central1")


def test_services_follows_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("pageToken") == "page2":
            return httpx.Response(200, json={"services": [svc("projects/p/locations/global/services/my-agent")]})
        return httpx.Response(200, json={"services": [svc("projects/p/locations/global/services/other")], "nextPageToken": "page2"})

    reg = registry.AgentRegistry(httpx.Client(transport=httpx.MockTransport(handler)), "p")
    assert len(reg.services("global")) == 2
    assert reg.missing_locations("my_agent") == []


def test_services_raises_on_api_error():
    reg = registry.AgentRegistry(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))), "p")
    with pytest.raises(httpx.HTTPStatusError):
        reg.missing_locations("my_agent")


def test_sync_fetches_card_from_regional_endpoint_and_normalizes_it():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/v1/card"):
            return httpx.Response(200, json={"preferredTransport": "HTTP_JSON"})
        if request.method == "GET":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"name": "projects/p/locations/global/operations/1", "done": True})

    reg = registry.AgentRegistry(httpx.Client(transport=httpx.MockTransport(handler)), "p", location="us")
    reg.sync("my_agent", "My description")

    card_request = requests[0]
    assert card_request.url.host == "geminidataanalytics.us.rep.googleapis.com"
    post = next(r for r in requests if r.method == "POST")
    assert post.url.params["serviceId"] == "my-agent"
    card = json.loads(post.content)["agentSpec"]["content"]
    assert card["preferredTransport"] == "HTTP+JSON"
    assert card["description"] == "My description"
    assert card["url"].endswith("/locations/us/dataAgents/my_agent")


def test_wait_times_out(monkeypatch):
    monkeypatch.setattr(registry, "OPERATION_TIMEOUT_SECONDS", 0)
    op = {"name": "projects/p/locations/global/operations/1", "done": False}
    reg = registry.AgentRegistry(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=op))), "p")
    with pytest.raises(TimeoutError):
        reg._wait(op, "global")


def test_wait_raises_operation_error():
    op = {"name": "projects/p/locations/global/operations/1", "done": True, "error": {"code": 6}}
    with pytest.raises(RuntimeError, match="operation failed"):
        registry.AgentRegistry(httpx.Client(), "p")._wait(op, "global")
