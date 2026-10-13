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
