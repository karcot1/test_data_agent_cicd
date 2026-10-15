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

"""Gemini Data Analytics endpoints, which differ between global and regional agents."""

from __future__ import annotations

from google.api_core.client_options import ClientOptions
from google.cloud import geminidataanalytics

# Multi-regions served through regional endpoints (REP).
MULTI_REGIONS = ("us", "eu")


def gda_host(location: str) -> str:
    """Return the Gemini Data Analytics hostname that serves agents in `location`.

    Agents in a multi-region (`us`, `eu`) or a region are only reachable through their
    regional endpoint; the global endpoint rejects them with 403.
    """
    location = location.lower()
    if location == "global":
        return "geminidataanalytics.googleapis.com"
    if location in MULTI_REGIONS:
        return f"geminidataanalytics.{location}.rep.googleapis.com"
    return f"geminidataanalytics-{location}.googleapis.com"


def a2a_agent_url(project_id: str, location: str, agent_id: str) -> str:
    """Base URL of a Data Agent's A2A endpoint (append /v1/card for its agent card)."""
    location = location.lower()
    return (
        f"https://{gda_host(location)}/v1/a2a/projects/{project_id}"
        f"/locations/{location}/dataAgents/{agent_id}"
    )


def data_agent_client(credentials, location: str) -> geminidataanalytics.DataAgentServiceClient:
    """DataAgentServiceClient bound to the endpoint that serves `location`."""
    return geminidataanalytics.DataAgentServiceClient(
        credentials=credentials,
        client_options=ClientOptions(api_endpoint=gda_host(location)),
    )
