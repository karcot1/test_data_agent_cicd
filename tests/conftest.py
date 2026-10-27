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

"""Shared fixtures. All tests are offline: HTTP goes through httpx.MockTransport."""

import httpx
import pytest


@pytest.fixture
def mock_http():
    """Factory for httpx clients backed by a handler function; every client is closed after the test.

    Usage: client = mock_http(lambda request: httpx.Response(200, json={}))
    """
    clients = []

    def make(handler=lambda request: httpx.Response(200, json={}), **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()
