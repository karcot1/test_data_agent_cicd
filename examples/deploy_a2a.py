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

"""Send one A2A message to a deployed Data Agent.

    PROJECT_ID=<project> AGENT_ID=<agent_id> [LOCATION=global] python examples/deploy_a2a.py
"""

import asyncio
import os
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest
from a2a.utils import TransportProtocol
from google.auth import default
from google.auth.transport.requests import Request

credentials, default_project = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
PROJECT = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or default_project
LOCATION = os.environ.get("LOCATION", "global")
AGENT_ID = os.environ.get("AGENT_ID", "thelook_ecommerce_agent")

# Agents outside "global" are only served by their regional endpoint.
if LOCATION == "global":
    HOST = "geminidataanalytics.googleapis.com"
elif LOCATION in ("us", "eu"):
    HOST = f"geminidataanalytics.{LOCATION}.rep.googleapis.com"
else:
    HOST = f"geminidataanalytics-{LOCATION}.googleapis.com"

# Managed A2A endpoint fronting your published Data Agent.
AGENT_URL = f"https://{HOST}/v1/a2a/projects/{PROJECT}/locations/{LOCATION}/dataAgents/{AGENT_ID}"


async def main():
    credentials.refresh(Request())
    async with httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
            # Bill quota to the agent's project instead of the credentials' default project.
            "x-goog-user-project": PROJECT,
        },
        timeout=120.0,
    ) as http:
        # 1. Discovery: fetch the A2A agent card (GetAgentCard).
        resolver = A2ACardResolver(httpx_client=http, base_url=AGENT_URL, agent_card_path="v1/card")
        card = await resolver.get_agent_card()
        print("Agent:", card.name, "-", card.description)

        # 2. Connect: bind an A2A client to that card.
        config = ClientConfig(
            httpx_client=http,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON, TransportProtocol.JSONRPC],
            streaming=False,  # True -> SendStreamingMessage
        )
        client = ClientFactory(config=config).create(card)

        # 3. Send an A2A message (SendMessage).
        # Don't pass a raw UUID as context_id for a new conversation: the backend creates
        # the conversation and returns its full resource name
        # (projects/{project}/locations/{location}/conversations/{id}).
        # Pass that returned context_id on later turns to keep the history.
        payload = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                parts=[Part(text="What data do you have access to?")],
                message_id=uuid4().hex,
            )
        )
        async for event in client.send_message(request=payload):
            print(event)


if __name__ == "__main__":
    asyncio.run(main())
