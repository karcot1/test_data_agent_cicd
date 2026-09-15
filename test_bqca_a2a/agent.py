import asyncio
import json
import random
import os
from google.adk.integrations.agent_registry import AgentRegistry
from google.auth import default

config_path = "test_bqca_a2a/config.json" if os.path.exists("test_bqca_a2a/config.json") else "config.json"
with open(config_path) as f:
    llm_config = json.load(f)

PROJECT_ID = llm_config["PROJECT_ID"]
MODEL = llm_config["MODEL"]
MODEL_REGION = llm_config["MODEL_REGION"]

AGENT_NAME = "projects/gapinc-sandbox/locations/global/agents/agentregistry-00000000-0000-0000-1d75-ebe14ae5ecf2"
LOCATION = "global"

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
registry = AgentRegistry(project_id=PROJECT_ID, location=LOCATION)

remote_agent = registry.get_remote_a2a_agent(AGENT_NAME)

root_agent = Agent(
  name="test_no_code_agent",
  description=(
    "BigQuery CA agent answering questions about attached data."
  ),
  model=Gemini(
    model="gemini-3.6-flash",
    retry_options=types.HttpRetryOptions(attempts=3),
  ),
  sub_agents=[remote_agent]
)