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

"""Grant Gemini Enterprise agent identities the IAP egress roles needed to call Data Agents."""

from __future__ import annotations

import httpx

DISCOVERY_API = "https://discoveryengine.googleapis.com/v1alpha"
CRM_API = "https://cloudresourcemanager.googleapis.com/v1"
IAP_API = "https://iap.googleapis.com/v1"

# roles/iap.viewer carries iap.resources.egressViaIAP (checked by agentGateways with IAP policy V2);
# roles/iap.egressor carries iap.webServiceVersions.egressViaIAP.
PROJECT_ROLES = ("roles/iap.viewer", "roles/iap.egressor")
IAP_WEB_ROLES = ("roles/iap.egressor",)


def matches_agent(ge_agent: dict, agent_ids: set[str]) -> bool:
    display_name = ge_agent.get("displayName", "")
    card_raw = ge_agent.get("a2aAgentDefinition", {}).get("jsonAgentCard", "")
    return any(
        display_name == agent_id
        or display_name.startswith(f"{agent_id} ")
        or f"/dataAgents/{agent_id}" in card_raw
        for agent_id in agent_ids
    )


def missing_members(policy: dict, roles_to_members: dict[str, set[str]]) -> dict[str, set[str]]:
    """Return, per role, the members not yet in that role's unconditional binding."""
    missing: dict[str, set[str]] = {}
    for role, members in roles_to_members.items():
        binding = next(
            (b for b in policy.get("bindings", []) if b.get("role") == role and not b.get("condition")),
            None,
        )
        existing = set(binding.get("members", [])) if binding else set()
        if members - existing:
            missing[role] = members - existing
    return missing


def add_members(policy: dict, missing: dict[str, set[str]]) -> dict:
    """Add-only merge of members into the policy's unconditional bindings (mutates and returns policy)."""
    bindings = policy.setdefault("bindings", [])
    for role, members in missing.items():
        binding = next(
            (b for b in bindings if b.get("role") == role and not b.get("condition")), None
        )
        if binding is None:
            binding = {"role": role, "members": []}
            bindings.append(binding)
        binding["members"] = sorted(set(binding.get("members", [])) | members)
    return policy


def ensure_bindings(
    client: httpx.Client,
    resource_url: str,
    roles_to_members: dict[str, set[str]],
    dry_run: bool = False,
) -> dict[str, set[str]]:
    resp = client.post(f"{resource_url}:getIamPolicy", json={})
    resp.raise_for_status()
    policy = resp.json()

    missing = missing_members(policy, roles_to_members)
    if not missing:
        print(f"    [SKIP] all bindings present on {resource_url}")
        return missing

    for role, members in missing.items():
        verb = "would add" if dry_run else "adding"
        print(f"    [MODIFY] {verb} {len(members)} member(s) to {role} on {resource_url}")
    if not dry_run:
        set_resp = client.post(
            f"{resource_url}:setIamPolicy", json={"policy": add_members(policy, missing)}
        )
        if set_resp.status_code >= 400:
            raise RuntimeError(
                f"setIamPolicy failed ({set_resp.status_code}) on {resource_url}: {set_resp.text}"
            )
    return missing


def discover_principals(client: httpx.Client, project_number: str, agent_ids: set[str]) -> set[str]:
    engines_resp = client.get(
        f"{DISCOVERY_API}/projects/{project_number}/locations/global"
        "/collections/default_collection/engines"
    )
    engines_resp.raise_for_status()

    principals: set[str] = set()
    for engine in engines_resp.json().get("engines", []):
        agents_resp = client.get(
            f"{DISCOVERY_API}/{engine['name']}/assistants/default_assistant/agents"
        )
        agents_resp.raise_for_status()
        for ge_agent in agents_resp.json().get("agents", []):
            if not matches_agent(ge_agent, agent_ids):
                continue
            spiffe = ge_agent.get("agentIdentityInfo", {}).get("spiffeId", "")
            if spiffe.startswith("//"):
                principals.add(f"principal:{spiffe}")
                print(f"    GE agent '{ge_agent.get('displayName')}' -> principal:{spiffe}")
    return principals


def configure_egress(
    client: httpx.Client, project_id: str, agent_ids: set[str], dry_run: bool = False
) -> None:
    proj_resp = client.get(f"{CRM_API}/projects/{project_id}")
    proj_resp.raise_for_status()
    project_number = str(proj_resp.json()["projectNumber"])

    principals = discover_principals(client, project_number, agent_ids)
    if not principals:
        print("    No matching agents registered in Gemini Enterprise yet; nothing to bind.")
        return

    ensure_bindings(
        client,
        f"{CRM_API}/projects/{project_id}",
        {role: principals for role in PROJECT_ROLES},
        dry_run,
    )
    ensure_bindings(
        client,
        f"{IAP_API}/projects/{project_number}/iap_web",
        {role: principals for role in IAP_WEB_ROLES},
        dry_run,
    )
