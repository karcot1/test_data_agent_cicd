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

import pytest
from google.auth.credentials import AnonymousCredentials

from deployer import endpoints


@pytest.mark.parametrize("location, host", [
    ("global", "geminidataanalytics.googleapis.com"),
    ("us", "geminidataanalytics.us.rep.googleapis.com"),
    ("EU", "geminidataanalytics.eu.rep.googleapis.com"),
    ("us-central1", "geminidataanalytics-us-central1.googleapis.com"),
])
def test_gda_host(location, host):
    assert endpoints.gda_host(location) == host


def test_a2a_agent_url_uses_regional_host():
    assert endpoints.a2a_agent_url("p", "us", "a") == (
        "https://geminidataanalytics.us.rep.googleapis.com/v1/a2a/projects/p/locations/us/dataAgents/a"
    )


def test_data_agent_client_uses_regional_endpoint():
    client = endpoints.data_agent_client(AnonymousCredentials(), "eu")
    assert client.api_endpoint == "geminidataanalytics.eu.rep.googleapis.com"
