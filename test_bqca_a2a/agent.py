import asyncio
import json
import random
import os
from functools import cached_property
from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.integrations.agent_registry import AgentRegistry
from google.genai import Client, types
from google.auth import default

config_path = "test_bqca_a2a/config.json" if os.path.exists("test_bqca_a2a/config.json") else "config.json"
with open(config_path) as f:
    llm_config = json.load(f)

PROJECT_ID = llm_config["PROJECT_ID"]
MODEL = llm_config["MODEL"]
MODEL_REGION = llm_config["MODEL_REGION"]
AGENT_REGISTRY_LOCATION = llm_config.get("AGENT_REGISTRY_LOCATION", "global")

AGENT_NAME = "projects/gapinc-sandbox/locations/global/agents/agentregistry-00000000-0000-0000-1d75-ebe14ae5ecf2"

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
registry = AgentRegistry(project_id=PROJECT_ID, location=AGENT_REGISTRY_LOCATION)

remote_agent = registry.get_remote_a2a_agent(AGENT_NAME)


class GlobalGemini(Gemini):
    @cached_property
    def api_client(self) -> Client:
        return Client(vertexai=True, location="global")


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