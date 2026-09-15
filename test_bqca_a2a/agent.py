import json
import os
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini

from google.adk.integrations.agent_registry import AgentRegistry
from google.auth import default

# Load config (support both local repo root and container /app)
config_path = "test_bqca_a2a/config.json" if os.path.exists("test_bqca_a2a/config.json") else "config.json"
with open(config_path) as f:
    llm_config = json.load(f)

PROJECT_ID = llm_config["PROJECT_ID"]
AGENT_NAME = "projects/gapinc-sandbox/locations/us/agents/agentregistry-00000000-0000-0000-ecc8-0ec40c48e7c7"
AGENT_RESGISTRY_LOCATION = llm_config["AGENT_RESGISTRY_LOCATION"]
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
registry = AgentRegistry(project_id=PROJECT_ID, location=LOCATION)

remote_agent = registry.get_remote_a2a_agent(AGENT_NAME)

sample_agent = Agent(
  name="test agent registry connectivity",
  description=(
    "test"
  ),
  model=Gemini(
    model="gemini-3.6-flash",
    retry_options=types.HttpRetryOptions(attempts=3),
  ),
  sub_agents=[remote_agent]
)