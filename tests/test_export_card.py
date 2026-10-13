import os

import pytest

from deployer import specs
from deployer.export_card import parse_source

P, L = "my-project", "global"


def public_card():
    """Shape of a card copied from the BigQuery UI: no BQ Dataset Information extension."""
    return {
        "name": "ui_copy",
        "capabilities": {
            "extensions": [
                {"description": "Datasource references extension", "params": {}},
                {"description": "GDA Stateless extension", "params": {}},
            ]
        },
    }


def test_card_without_bq_extension_rejected():
    with pytest.raises(specs.SpecError, match="export_card"):
        specs.parse_agent_card(public_card(), "ui.json", P, L)


def test_card_with_no_tables_rejected():
    data = public_card()
    data["capabilities"]["extensions"].append(
        {"description": "BQ Dataset Information", "params": {"system_instructions": "x", "table_info": []}}
    )
    with pytest.raises(specs.SpecError, match="no tables"):
        specs.parse_agent_card(data, "empty.json", P, L)


@pytest.mark.parametrize("source", [
    "projects/p1/locations/global/dataAgents/agent_9c-1",
    "https://geminidataanalytics.googleapis.com/v1/a2a/projects/p1/locations/global/dataAgents/agent_9c-1",
    "https://geminidataanalytics.googleapis.com/v1beta/a2a/projects/p1/locations/global/dataAgents/agent_9c-1/v1/card",
])
def test_parse_source_accepts_name_or_url(source):
    assert parse_source(source) == ("p1", "global", "agent_9c-1")


def test_parse_source_rejects_garbage():
    with pytest.raises(ValueError):
        parse_source("not-a-resource")
