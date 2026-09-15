import asyncio
import json
import os
import random
import subprocess
from functools import cached_property
import httpx
import google.auth
import google.oauth2.credentials
from google.auth.transport.requests import AuthorizedSession, Request
from a2a.client.client_factory import ClientFactory as A2AClientFactory
from google.adk.a2a import _compat
from google.adk.agents import Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.integrations.agent_registry import AgentRegistry
from google.adk.models.google_llm import Gemini
from google.genai import Client, types

config_path = "test_bqca_a2a/config.json" if os.path.exists("test_bqca_a2a/config.json") else "config.json"
with open(config_path) as f:
    llm_config = json.load(f)

PROJECT_ID = llm_config["PROJECT_ID"]
MODEL = llm_config["MODEL"]
MODEL_REGION = llm_config["MODEL_REGION"]
AGENT_REGISTRY_LOCATION = llm_config.get("AGENT_REGISTRY_LOCATION", "global")

AGENT_NAME = "projects/gapinc-sandbox/locations/global/agents/agentregistry-00000000-0000-0000-1d75-ebe14ae5ecf2"

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"


def get_gcp_credentials():
    """Returns Google Cloud credentials with cloud-platform scope (with local CLI fallback)."""
    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())
        return creds
    except Exception:
        token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
        return google.oauth2.credentials.Credentials(token=token)


class GoogleCloudAuth(httpx.Auth):
    """httpx Auth handler that attaches a Google Cloud OAuth Bearer token to A2A requests."""

    def __init__(self):
        self.credentials = get_gcp_credentials()

    def auth_flow(self, request):
        if not self.credentials.valid:
            try:
                self.credentials.refresh(Request())
            except Exception:
                token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
                self.credentials = google.oauth2.credentials.Credentials(token=token)
        request.headers["Authorization"] = f"Bearer {self.credentials.token}"
        request.headers["x-goog-user-project"] = PROJECT_ID
        yield request


class AuthRemoteA2aAgent(RemoteA2aAgent):
    """RemoteA2aAgent with Google Cloud OAuth authentication, non-streaming REST transport, and HTTP+JSON protocol normalization."""

    async def _ensure_httpx_client(self) -> httpx.AsyncClient:
        if self._httpx_client is None:
            self._httpx_client = httpx.AsyncClient(
                auth=GoogleCloudAuth(),
                timeout=httpx.Timeout(timeout=600.0),
            )
            self._httpx_client_needs_cleanup = True
            self._a2a_client_factory = A2AClientFactory(
                config=_compat.make_client_config(
                    httpx_client=self._httpx_client,
                    streaming=False,
                    polling=False,
                )
            )
        return self._httpx_client

    async def _ensure_resolved(self, ctx=None):
        if self._agent_card and getattr(self._agent_card, "supported_interfaces", None):
            for iface in self._agent_card.supported_interfaces:
                if iface.protocol_binding == "HTTP_JSON":
                    iface.protocol_binding = "HTTP+JSON"
        return await super()._ensure_resolved(ctx)


creds = get_gcp_credentials()
registry = AgentRegistry(project_id=PROJECT_ID, location=AGENT_REGISTRY_LOCATION)
registry._credentials = creds
registry._session = AuthorizedSession(credentials=creds)

base_remote_agent = registry.get_remote_a2a_agent(AGENT_NAME)
remote_agent = AuthRemoteA2aAgent(
    name=base_remote_agent.name,
    description=base_remote_agent.description,
    agent_card=base_remote_agent._agent_card,
)


class GlobalGemini(Gemini):
    @cached_property
    def api_client(self) -> Client:
        return Client(
            vertexai=True,
            project=PROJECT_ID,
            location="global",
            credentials=get_gcp_credentials(),
        )


llm_model = (
    GlobalGemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    )
    if MODEL_REGION == "global"
    else Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    )
)

root_agent = Agent(
    name="test_bqca_a2a_agent",
    description="Orchestrator agent that answers data questions using the BigQuery Conversational Analytics A2A agent.",
    instruction="You are a helpful data assistant. Delegate all data analysis and BigQuery questions to the test_no_code_agent sub-agent.",
    model=llm_model,
    sub_agents=[remote_agent],
)