import os
import json
import re
import argparse

# from google.cloud import geminidataanalytics

# data_agent_client = geminidataanalytics.DataAgentServiceClient()

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

print("------------------------------------")
print(f"Project ID: {project_id}")
print("------------------------------------")
print(f"File Path: {file_path}")

with open(file_path, 'r', encoding='utf-8') as file:
    data = json.load(file)

data_agent_id = f"{data.get('name')}_cicd"

print("------------------------------------")
print(f"Agent Name: {data_agent_id}")
url = data.get('url')

print("------------------------------------")
print(f"URL: {url}")

# Define a regex pattern with capture groups for project, location, and data_agent
pattern = r"/projects/([^/]+)/locations/([^/]+)/dataAgents/([^/]+)"

match = re.search(pattern, url)

if match:
    location = match.group(2)
    agent_id = match.group(3)
    
    print("------------------------------------")
    print(f"location = '{location}'")
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

# # Create BigQueryTableReference objects dynamically
# table_references = []
# for table in tables:
#     proj_id, ds_id, tbl_id = table.split('.', 2)
#     table_references.append(
#         geminidataanalytics.BigQueryTableReference(
#             project_id=proj_id,
#             dataset_id=ds_id,
#             table_id=tbl_id,
#         )
#     )

# # BigQuery table data sources
# datasource_references = geminidataanalytics.DatasourceReferences(
#     bq=geminidataanalytics.BigQueryTableReferences(table_references=table_references)
# )

# # Context setup for stateful chat
# published_context = geminidataanalytics.Context(
#     system_instruction=system_instructions,
#     datasource_references=datasource_references,
#     example_queries=example_queries,
#     options=geminidataanalytics.ConversationOptions(
#         analysis=geminidataanalytics.AnalysisOptions(
#             python=geminidataanalytics.AnalysisOptions.Python(
#                 enabled=False
#             )
#         )
#     ),
# )

# data_agent = geminidataanalytics.DataAgent(
#     data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
#         published_context=published_context
#     ),
# )

# # Create the agent
# data_agent_client.create_data_agent(request=geminidataanalytics.CreateDataAgentRequest(
#     parent=f"projects/{project_id}/locations/{location}",
#     data_agent_id=data_agent_id,
#     data_agent=data_agent,
# ))