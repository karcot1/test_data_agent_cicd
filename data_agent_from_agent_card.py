import os
import json
import re
import argparse
from google.cloud import geminidataanalytics
import google.auth
import google.auth.transport.requests
import httpx

data_agent_client = geminidataanalytics.DataAgentServiceClient()

credentials, project_id = google.auth.default(
    scopes=["https://googleapis.com"]
)
auth_request = google.auth.transport.requests.Request()
credentials.refresh(auth_request)

# Create the parser
parser = argparse.ArgumentParser(description="Process deployment parameters.")

# Add arguments (both positional and named flags are supported)
parser.add_argument("--project_id", required=True, help="The GCP project ID")
parser.add_argument("--file_path", required=True, help="The location of the A2A card to parse")

# Parse arguments
args = parser.parse_args()

# Load the JSON data from the file
project_id = args.project_id
file_path = args.file_path
location = "global"

print("------------------------------------")
print(f"Project ID: {project_id}")
print("------------------------------------")
print(f"File Path: {file_path}")
print("------------------------------------")
print(f"Location: {location}")

with open(file_path, 'r', encoding='utf-8') as file:
    data = json.load(file)

data_agent_id = f"{data.get('name')}_no_code_agent"

print("------------------------------------")
print(f"Agent Name: {data_agent_id}")
url = data.get('url')

print("------------------------------------")
print(f"URL: {url}")

# Define a regex pattern with capture groups for project, location, and data_agent
pattern = r"/projects/([^/]+)/locations/([^/]+)/dataAgents/([^/]+)"

match = re.search(pattern, url)

if match:
    agent_id = match.group(3)

    print("------------------------------------")
    print(f"agent_id = '{agent_id}'")
else:
    raise ValueError("Malformed JSON: Could not parse the URL pattern.")

capabilities = data.get('capabilities')

extensions = None
if capabilities and 'extensions' in capabilities:
    for ext in capabilities['extensions']:
        if ext.get('description') == "BQ Dataset Information":
            extensions = ext
            break

params = extensions.get('params', {}) if extensions else {}
system_instructions = params.get('system_instructions', '')
print("------------------------------------")
print(system_instructions)
print("------------------------------------")
example_queries = params.get('example_queries', {})
print(example_queries)
print("------------------------------------")
tables = params.get('table_info', [])

parsed_example_queries = []

for nl_prompt, query_data in example_queries.items():
    # Handle list format: [SQL_query, *optional_params]
    if isinstance(query_data, list) and len(query_data) > 0:
        sql = query_data[0]
        params_list = query_data[1:] if len(query_data) > 1 else []
    else:
        sql = str(query_data)
        params_list = []

    # Create the ExampleQuery object
    parsed_example_queries.append(
        geminidataanalytics.ExampleQuery(
            natural_language_question=nl_prompt,
            sql_query=sql,
            parameters=params_list
        )
    )


# Create BigQueryTableReference objects dynamically
table_references = []
for table in tables:
    proj_id, ds_id, tbl_id = table.split('.', 2)
    table_references.append(
        geminidataanalytics.BigQueryTableReference(
            project_id=proj_id,
            dataset_id=ds_id,
            table_id=tbl_id,
        )
    )

# BigQuery table data sources
datasource_references = geminidataanalytics.DatasourceReferences(
    bq=geminidataanalytics.BigQueryTableReferences(table_references=table_references)
)

# Context setup for stateful chat
published_context = geminidataanalytics.Context(
    system_instruction=system_instructions,
    datasource_references=datasource_references,
    example_queries=parsed_example_queries,
    options=geminidataanalytics.ConversationOptions(
        analysis=geminidataanalytics.AnalysisOptions(
            python=geminidataanalytics.AnalysisOptions.Python(
                enabled=False
            )
        )
    ),
)

data_agent = geminidataanalytics.DataAgent(
    data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
        published_context=published_context
    ),
)

# Create/Update the agent
try:
    operation = data_agent_client.create_data_agent(
        request=geminidataanalytics.CreateDataAgentRequest(
            parent=f"projects/{project_id}/locations/{location}",
            data_agent_id=data_agent_id,
            data_agent=data_agent,
        )
    )
    print("Created DataAgent:", operation.result().name)
except exceptions.AlreadyExists:
    print(f"DataAgent '{data_agent_id}' already exists. Updating in-place...")
    operation = data_agent_client.update_data_agent(
        request=geminidataanalytics.UpdateDataAgentRequest(
            data_agent=data_agent,
            update_mask=field_mask_pb2.FieldMask(paths=["data_analytics_agent"]),
        )
    )
    print("Updated DataAgent:", operation.result().name)

# Register the agent with A2A protocol in Agent Registry
A2A_CARD_URL = f"https://geminidataanalytics.googleapis.com/v1beta/projects/{project_id}/locations/{location}/agents/{data_agent_id}/.well-known/agent-card.json"
REGISTRY_URL = f"https://geminidataanalytics.googleapis.com/v1beta/projects/{project_id}/locations/{location}/agents"

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
    "displayName": f"{data_agent_id} [BigQuery Conversational Analytics Agent]",
    "description": "Enterprise data agent for natural language BigQuery reporting.",
    "agentType": "CUSTOM_VIA_A2A",
    "agentCardJson": card_response.text  # The raw JSON string containing the A2A spec
}

# 5. Push directly into the Agent Registry
registry_response = httpx.post(REGISTRY_URL, headers=headers, json=registry_payload)
registry_response.raise_for_status()

print("Successfully registered agent! Server response:")
print(registry_response.json())