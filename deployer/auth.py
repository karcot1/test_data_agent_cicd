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

"""Google Cloud authentication helpers shared by all deployer modules."""

import subprocess

import google.auth
import google.auth.transport.requests
import google.oauth2.credentials


def get_credentials():
    """Return refreshed credentials, preferring ADC and falling back to the gcloud CLI token."""
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
        return credentials
    except Exception:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True
        ).strip()
        return google.oauth2.credentials.Credentials(token=token)


def auth_headers(credentials, project_id: str) -> dict[str, str]:
    """Build REST headers with a bearer token and quota project."""
    return {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
        "x-goog-user-project": project_id,
    }
