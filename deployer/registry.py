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

"""Sync no-code Data Agents' live A2A cards into Google Cloud Agent Registry."""

from __future__ import annotations

import time
from collections.abc import Sequence

import httpx

from deployer.endpoints import a2a_agent_url

DEFAULT_REGISTRY_LOCATIONS = ("global",)
DEFAULT_DESCRIPTION = "Enterprise data agent for natural language BigQuery reporting."
REGISTRY_API = "https://agentregistry.googleapis.com/v1"
MAX_NAME_LEN = 63
OPERATION_POLL_SECONDS = 1
OPERATION_TIMEOUT_SECONDS = 60


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
    def __init__(
        self,
        client: httpx.Client,
        project_id: str,
        location: str = "global",
        registry_locations: Sequence[str] = DEFAULT_REGISTRY_LOCATIONS,
    ):
        self.client = client
        self.project_id = project_id
        self.location = location  # Data Agent location
        # Agent Registry entries are per-location and not replicated between locations.
        self.registry_locations = tuple(dict.fromkeys(registry_locations))
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
            loc
            for loc in self.registry_locations
            if find_service(self.services(loc), agent_id) is None
        ]

    def _build_payload(self, agent_id: str, description: str) -> dict:
        card_url = a2a_agent_url(self.project_id, self.location, agent_id)
        resp = self.client.get(f"{card_url}/v1/card")
        resp.raise_for_status()
        card = resp.json()

        # Fill fields Agent Registry requires for indexing.
        card["name"] = card.get("name") or agent_id
        card["description"] = card.get("description") or description or DEFAULT_DESCRIPTION
        card["version"] = card.get("version") or "1.0.0"
        card["url"] = card.get("url") or card_url
        # Some Gemini Data Analytics endpoints return "HTTP_JSON"; a2a-sdk expects "HTTP+JSON".
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
        deadline = time.monotonic() + OPERATION_TIMEOUT_SECONDS
        while not op_data.get("done"):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Agent Registry operation {op_name} in '{reg_location}' did not finish "
                    f"within {OPERATION_TIMEOUT_SECONDS}s."
                )
            time.sleep(OPERATION_POLL_SECONDS)
            resp = self.client.get(f"{REGISTRY_API}/{op_name}")
            resp.raise_for_status()
            op_data = resp.json()
        if "error" in op_data:
            raise RuntimeError(
                f"Agent Registry operation failed in '{reg_location}': {op_data['error']}"
            )

    def sync(self, agent_id: str, description: str = "") -> None:
        payload = self._build_payload(agent_id, description)
        for reg_location in self.registry_locations:
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
