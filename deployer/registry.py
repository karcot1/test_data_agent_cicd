"""Sync no-code Data Agents' live A2A cards into Google Cloud Agent Registry."""

from __future__ import annotations

import time

import httpx

from deployer.specs import DEFAULT_DESCRIPTION

REGISTRY_LOCATIONS = ("global", "us-central1")
REGISTRY_API = "https://agentregistry.googleapis.com/v1"
GDA_A2A_API = "https://geminidataanalytics.googleapis.com/v1/a2a"
MAX_NAME_LEN = 63


def service_id_for(agent_id: str) -> str:
    return agent_id.replace("_", "-")[:MAX_NAME_LEN].rstrip("-")


def display_name_for(agent_id: str) -> str:
    return f"{agent_id} [BQCA Agent]"[:MAX_NAME_LEN].rstrip()


def find_service(services: list[dict], agent_id: str) -> dict | None:
    """Match an existing registry service by its card URL or by service ID."""
    url_suffix = f"/dataAgents/{agent_id}"
    name_suffix = f"/services/{service_id_for(agent_id)}"
    for svc in services:
        svc_url = svc.get("agentSpec", {}).get("content", {}).get("url", "") or ""
        if svc_url.endswith(url_suffix) or svc.get("name", "").endswith(name_suffix):
            return svc
    return None


class AgentRegistry:
    def __init__(self, client: httpx.Client, project_id: str, location: str = "global"):
        self.client = client
        self.project_id = project_id
        self.location = location  # Data Agent location
        self._services: dict[str, list[dict]] = {}

    def services(self, reg_location: str) -> list[dict]:
        if reg_location not in self._services:
            url = f"{REGISTRY_API}/projects/{self.project_id}/locations/{reg_location}/services"
            resp = self.client.get(url)
            self._services[reg_location] = (
                resp.json().get("services", []) if resp.status_code == 200 else []
            )
        return self._services[reg_location]

    def missing_locations(self, agent_id: str) -> list[str]:
        return [
            loc for loc in REGISTRY_LOCATIONS if find_service(self.services(loc), agent_id) is None
        ]

    def _build_payload(self, agent_id: str, card_data: dict) -> dict:
        card_url = (
            f"{GDA_A2A_API}/projects/{self.project_id}/locations/{self.location}"
            f"/dataAgents/{agent_id}"
        )
        resp = self.client.get(f"{card_url}/v1/card")
        resp.raise_for_status()
        card = resp.json()

        # Fill fields Agent Registry requires for indexing.
        card["name"] = card.get("name") or agent_id
        card["description"] = (
            card.get("description") or card_data.get("description") or DEFAULT_DESCRIPTION
        )
        card["version"] = card.get("version") or "1.0.0"
        card["url"] = card.get("url") or card_url
        # Gemini Data Analytics returns "HTTP_JSON"; a2a-sdk 1.x expects "HTTP+JSON".
        card["preferredTransport"] = "HTTP+JSON"

        return {
            "displayName": display_name_for(agent_id),
            "description": card["description"],
            "agentSpec": {"type": "A2A_AGENT_CARD", "content": card},
        }

    def _wait(self, op_data: dict, reg_location: str) -> None:
        op_name = op_data.get("name", "")
        if "/operations/" not in op_name:
            return
        for _ in range(30):
            if op_data.get("done"):
                break
            time.sleep(1)
            resp = self.client.get(f"{REGISTRY_API}/{op_name}")
            resp.raise_for_status()
            op_data = resp.json()
        if "error" in op_data:
            raise RuntimeError(
                f"Agent Registry operation failed in '{reg_location}': {op_data['error']}"
            )

    def sync(self, agent_id: str, card_data: dict) -> None:
        payload = self._build_payload(agent_id, card_data)
        for reg_location in REGISTRY_LOCATIONS:
            existing = find_service(self.services(reg_location), agent_id)
            if existing:
                print(f"    Agent Registry ({reg_location}): updating {existing['name']}")
                resp = self.client.patch(
                    f"{REGISTRY_API}/{existing['name']}",
                    params={"updateMask": "agentSpec,displayName,description"},
                    json=payload,
                )
            else:
                print(f"    Agent Registry ({reg_location}): creating {service_id_for(agent_id)}")
                resp = self.client.post(
                    f"{REGISTRY_API}/projects/{self.project_id}/locations/{reg_location}/services",
                    params={"serviceId": service_id_for(agent_id)},
                    json=payload,
                )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Agent Registry API error ({resp.status_code}) in '{reg_location}': {resp.text}"
                )
            self._wait(resp.json(), reg_location)
