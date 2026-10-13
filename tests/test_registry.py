import httpx

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
