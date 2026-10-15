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

"""Low-code Data Agent definition: Google Trends analyst.

Deployed by `python -m deployer`. This module only describes the desired agent;
it must define AGENT_ID and build_agent(project_id, location).
"""

from google.cloud import geminidataanalytics

AGENT_ID = "google_trends_agent"

SYSTEM_INSTRUCTION = """
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

TABLES = [
    ("bigquery-public-data", "google_trends", "top_terms"),
    ("bigquery-public-data", "google_trends", "top_rising_terms"),
]


def build_agent(project_id: str, location: str) -> geminidataanalytics.DataAgent:
    """Return the desired DataAgent.

    The deployer fills in `name` from AGENT_ID and copies published_context to
    staging_context, so BigQuery Studio shows what was deployed.
    """
    return geminidataanalytics.DataAgent(
        display_name="Google Trends analyst",
        description="Answers questions about top and rising Google Search terms by US metro area.",
        data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
            published_context=geminidataanalytics.Context(
                system_instruction=SYSTEM_INSTRUCTION,
                datasource_references=geminidataanalytics.DatasourceReferences(
                    bq=geminidataanalytics.BigQueryTableReferences(
                        table_references=[
                            geminidataanalytics.BigQueryTableReference(
                                project_id=p, dataset_id=d, table_id=t
                            )
                            for p, d, t in TABLES
                        ]
                    )
                ),
                options=geminidataanalytics.ConversationOptions(
                    analysis=geminidataanalytics.AnalysisOptions(
                        python=geminidataanalytics.AnalysisOptions.Python(enabled=False)
                    )
                ),
            )
        ),
    )
