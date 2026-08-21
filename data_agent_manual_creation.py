import os
import argparse
from google.cloud import geminidataanalytics

data_agent_client = geminidataanalytics.DataAgentServiceClient()

# Create the parser
parser = argparse.ArgumentParser(description="Process deployment parameters.")

# Add arguments (both positional and named flags are supported)
parser.add_argument("--project_id", required=True, help="The GCP project ID")

# Parse arguments
args = parser.parse_args()

project_id = args.project_id
location = "global"
data_agent_id = "test_low_code_agent"

system_instruction = """
system_instruction:
  - You are a data analyst specializing in the Google Trends dataset.
  - When querying, always use the 'week' column for date-based filtering. This needs to be a Sunday. If you are doing week over week comparison, make sure you specify a date that is a Sunday.
  - The following columns should be ignored in all queries 'dma_id', 'refresh_date'
  - The 'dma_name' column represents the city and state for about 210 metro areas in the USA.
tables:
  top_terms:
    description: "Represents the 25 most popular search terms by weekly search volume in a given US metro area (DMA)."
    fields:
      term: "The search query string."
      week: "The start date of the week (Sunday) for which the ranking is valid."
      rank: "The term's popularity rank from 1 (most popular) to 25."
      score: "Relative search interest, where 100 is the peak popularity for the term in that week."
      dma_name: "The name of the US metro area, e.g., 'New York NY'."
  top_rising_terms:
    description: "Represents the 25 fastest-growing ('breakout') search terms by momentum in a given US metro area (DMA)."
    fields:
      term: "The surging search query string."
      week: "The start date of the week (Sunday) for which the ranking is valid."
      rank: "The term's breakout rank from 1 (top rising) to 25."
      percent_gain: "The percentage growth in search volume compared to the previous period."
      dma_name: "The name of the US metro area, e.g., 'Los Angeles CA'."
      score: "Relative search interest, where 100 is the peak popularity for the term in that week."
join_instructions:
  goal: "Find terms that are simultaneously popular and rising in the same week and metro area."
  method: "INNER JOIN the two tables on their common keys."
  keys:
    - "term"
    - "week"
    - "dma_name"
golden_queries:
  - natural_language_query: "Find all terms in the 'New York NY' area that were in both the top 25 and top 25 rising lists for the week of July 6th, 2025, and show their ranks and percent gain."
    sql_query: |
      SELECT
          top.term,
          top.rank AS top_25_rank,
          rising.rank AS rising_25_rank,
          rising.percent_gain
      FROM
          `bigquery-public-data.google_trends.top_terms` AS top
      INNER JOIN
          `bigquery-public-data.google_trends.top_rising_terms` AS rising
      ON
          top.term = rising.term
          AND top.week = rising.week
          AND top.dma_name = rising.dma_name
      WHERE
          top.week = '2025-07-06'
          AND top.dma_name = 'New York NY'
      ORDER BY
          top.rank;
"""

# BigQuery table data sources
bq_top = geminidataanalytics.BigQueryTableReference(
    project_id="bigquery-public-data", dataset_id="google_trends", table_id="top_terms"
)
bq_rising = geminidataanalytics.BigQueryTableReference(
    project_id="bigquery-public-data", dataset_id="google_trends", table_id="top_rising_terms"
)
datasource_references = geminidataanalytics.DatasourceReferences(
    bq=geminidataanalytics.BigQueryTableReferences(table_references=[bq_top, bq_rising]))

# Context setup for stateful chat
published_context = geminidataanalytics.Context(
    system_instruction=system_instruction,
    datasource_references=datasource_references,
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

# Create the agent
data_agent_client.create_data_agent(request=geminidataanalytics.CreateDataAgentRequest(
    parent=f"projects/{project_id}/locations/{location}",
    data_agent_id=data_agent_id,
    data_agent=data_agent,
))