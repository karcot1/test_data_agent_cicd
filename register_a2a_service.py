import google.auth
import google.auth.transport.requests
import httpx

# 1. Setup GCP Authentication
credentials, project_id = google.auth.default(
    scopes=["https://googleapis.com"]
)
auth_request = google.auth.transport.requests.Request()
credentials.refresh(auth_request)

PROJECT_ID = "your-gcp-project-id"
LOCATION = "global"  # e.g., us-central1 or global
DATA_AGENT_ID = "your-bigquery-agent-id"

# 2. Endpoints
A2A_CARD_URL = f"https://googleapis.com{PROJECT_ID}/locations/{LOCATION}/agents/{DATA_AGENT_ID}/.well-known/agent-card.json"
REGISTRY_URL = f"https://googleapis.com{PROJECT_ID}/locations/{LOCATION}/agents"

headers = {
    "Authorization": f"Bearer {credentials.token}",
    "Content-Type": "application/json",
    "A2A-Extensions": "GcpResource"
}

# 3. Pull the live A2A Agent Card JSON from BigQuery
card_response = httpx.get(A2A_CARD_URL, headers=headers)
card_response.raise_for_status()
agent_card_json_data = card_response.json()

# 4. Format payload for Agent Registry
# Injecting the fetched layout into the register blueprint
registry_payload = {
    "displayName": "BigQuery Conversational Analytics Agent",
    "description": "Enterprise data agent for natural language BigQuery reporting.",
    "agentType": "CUSTOM_VIA_A2A",
    "agentCardJson": card_response.text  # The raw JSON string containing the A2A spec
}

# 5. Push directly into the Agent Registry
registry_response = httpx.post(REGISTRY_URL, headers=headers, json=registry_payload)
registry_response.raise_for_status()

print("Successfully registered agent! Server response:")
print(registry_response.json())