import asyncio
from uuid import uuid4
import httpx
from google.auth import default
from google.auth.transport.requests import Request
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.utils import TransportProtocol
from a2a.types import SendMessageRequest, Message, Part, Role

PROJECT  = "gapinc-sandbox"
LOCATION = "global"          # must match the endpoint host below
AGENT_ID = "test_no_code_agent"

# Managed A2A endpoint fronting your published CA data agent
AGENT_URL = (
    "https://geminidataanalytics.googleapis.com"
    f"/v1/a2a/projects/{PROJECT}/locations/{LOCATION}/dataAgents/{AGENT_ID}"
)

def get_bearer_token() -> str:
    creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    return creds.token

async def main():
    token = get_bearer_token()
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=120.0,
    ) as http:
        # 2. Discovery — fetch the real A2A Agent Card (GetAgentCard)
        resolver = A2ACardResolver(
            httpx_client=http, base_url=AGENT_URL, agent_card_path="v1/card"
        )
        card = await resolver.get_agent_card()
        print("Agent:", card.name, "—", card.description)

        # 3. Connect — bind an A2A client to that card
        config = ClientConfig(
            httpx_client=http,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON, TransportProtocol.JSONRPC],
            streaming=False,   # True → SendStreamingMessage
        )
        factory = ClientFactory(config=config)
        client = factory.create(card)

        # 4. Send an A2A message (SendMessage)
        # Note: Do not pass a raw UUID for context_id on a new conversation.
        # The backend creates the conversation and returns its full resource name
        # (e.g. projects/{project}/locations/{location}/conversations/{id}).
        # For subsequent turns, pass that returned context_id to maintain history.
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