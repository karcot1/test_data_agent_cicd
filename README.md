# BigQuery Data Agent & Vertex AI Reasoning Engine A2A CI/CD Deployment

This repository provides CI/CD pipelines, Terraform modules, and Python scripts for automatically provisioning **BigQuery Conversational Analytics (Gemini Data Analytics) Agents**, registering them in **Google Cloud Agent Registry**, and orchestrating them from a **Vertex AI Reasoning Engine (Agent Engine) BYOC container** using the **Agent-to-Agent (A2A) protocol** and **Google Agent Development Kit (ADK)**.

The repository supports three core workflows:
1. **No-Code Data Agent CI/CD (`data_agent_from_agent_card.py`)**: Promotes an agent created in BigQuery across environments (`dev`/`stage`/`prod`) using its A2A Agent Card (`agent_card.json`) and registers it in Google Cloud Agent Registry.
2. **Low-Code Data Agent CI/CD (`data_agent_manual_creation.py`)**: Defines and deploys a Gemini Data Analytics agent programmatically using Python (`google-cloud-geminidataanalytics` SDK).
3. **A2A Multi-Agent Orchestrator on Vertex AI Reasoning Engine (`test_bqca_a2a/`)**: Deploys a containerized Google ADK orchestrator agent (`test_bqca_a2a_agent`) on Vertex AI Reasoning Engine that discovers the BigQuery Conversational Analytics agent (`test_no_code_agent`) from Agent Registry and delegates natural language data analysis queries to it over authenticated A2A REST transport.

---

## 📁 Repository Structure

```text
.
├── agent_card.json                        # Source A2A Agent Card specification (metadata, table references, golden queries)
├── cloudbuild.yaml                        # Cloud Build pipeline for automated Data Agent provisioning & Agent Registry sync
├── data_agent_from_agent_card.py          # Deploys/updates Data Agent from agent_card.json and registers it in Agent Registry
├── data_agent_manual_creation.py          # Programmatic low-code Data Agent deployment script
├── deploy_a2a.py                          # Standalone A2A client reference script for testing Data Agent A2A endpoints
├── requirements.txt                       # Python dependencies for Data Agent CI/CD scripts
├── terraform/                             # Terraform configuration for Vertex AI Reasoning Engine BYOC deployment
│   ├── main.tf                            # google_vertex_ai_reasoning_engine resource definition
│   └── variables.tf                       # Terraform input variables (project_id, location, image_tag, etc.)
└── test_bqca_a2a/                         # Vertex AI Reasoning Engine BYOC A2A Orchestrator Agent
    ├── Dockerfile                         # Container image definition for Reasoning Engine
    ├── agent.py                           # ADK root_agent with AuthRemoteA2aAgent & GoogleCloudAuth
    ├── main.py                            # FastAPI entrypoint exposing ADK Reasoning Engine endpoints
    ├── config.json                        # Project, model, and Agent Registry configuration
    ├── requirements.txt                   # Container Python dependencies (google-adk[a2a,agent-identity], mcp, etc.)
    ├── cloudbuild.yaml                    # Cloud Build pipeline: builds image, pushes to Artifact Registry, runs Terraform apply
    ├── update_agent_cloudbuild.yaml       # Cloud Build pipeline for fast container-only updates on an existing Reasoning Engine
    └── update_agent_deployment.sh         # Helper script to PATCH an existing Reasoning Engine with a new container image tag
```

---

## 🚀 Key Components

### 1. `data_agent_from_agent_card.py` (No-Code Data Agent & Agent Registry CI/CD)
Dynamically provisions or updates a BigQuery Conversational Analytics Data Agent from `agent_card.json` and registers its live A2A endpoint in Google Cloud Agent Registry:
- **Datasource & Prompt Extraction**: Parses system instructions, golden SQL example queries (`ExampleQuery`), and fully-qualified BigQuery table references (`project.dataset.table`) from the `BQ Dataset Information` extension in `agent_card.json`.
- **Idempotent Creation & Soft-Delete Handling**: Calls `create_data_agent()` and automatically catches `google.api_core.exceptions.AlreadyExists` (including soft-deleted agents within the 30-day retention window), seamlessly falling back to `update_data_agent()` with an explicit `FieldMask`.
- **Live A2A Card Retrieval**: Fetches the live A2A Agent Card directly from the Gemini Data Analytics A2A endpoint:
  ```text
  https://geminidataanalytics.googleapis.com/v1/a2a/projects/{project_id}/locations/{location}/dataAgents/{data_agent_id}/v1/card
  ```
- **A2A Transport Normalization (`HTTP+JSON`)**: Normalizes `"preferredTransport"` from `"HTTP_JSON"` (returned by Gemini Data Analytics) to `"HTTP+JSON"` so that `a2a-sdk` 1.x (`TransportProtocol.HTTP_JSON`) recognizes the interface binding.
- **Google Cloud Agent Registry Synchronization**: Registers/updates the agent in `agentregistry.googleapis.com` across both `global` and regional (`us-central1`) endpoints:
  - Checks existing Agent Registry `/services` for matching interface URLs or service IDs to prevent duplicate URL conflicts.
  - Executes `PATCH` (if the service already exists) or `POST` (if creating a new service) and polls the returned Long-Running Operation (LRO) until completion.

### 2. `test_bqca_a2a/` (Vertex AI Reasoning Engine A2A Orchestrator)
Contains a containerized Google ADK agent (`test_bqca_a2a_agent`) deployed as a BYOC Vertex AI Reasoning Engine in `us-central1`:
- **Agent Registry Discovery**: Uses `google.adk.integrations.agent_registry.AgentRegistry` to dynamically discover the registered BigQuery Conversational Analytics agent (`test_no_code_agent`) by resource name.
- **`GoogleCloudAuth` (`httpx.Auth`)**: Automatically attaches a refreshed Google Cloud OAuth Bearer token (`Authorization: Bearer <token>`) and explicitly sets the consumer quota/billing header (`x-goog-user-project: <PROJECT_ID>`) on all outbound HTTP requests to `geminidataanalytics.googleapis.com`.
- **`AuthRemoteA2aAgent` (Subclass of `RemoteA2aAgent`)**:
  - Overrides `_ensure_httpx_client` to configure `A2AClientFactory` with `streaming=False, polling=False` so `a2a-sdk` uses the synchronous A2A v0.3 REST transport (`/v1/message:send`) rather than SSE streaming (`/v1/message:stream`).
  - Overrides `_ensure_resolved` to normalize any `"HTTP_JSON"` interface bindings to `"HTTP+JSON"` before client initialization.
- **Automated Deployment**:
  - `test_bqca_a2a/cloudbuild.yaml`: Builds the container image, pushes it to Artifact Registry (`us-docker.pkg.dev/${PROJECT_ID}/agent-repo/test_bqca_a2a:${SHORT_SHA}`), and applies Terraform (`terraform/`) to provision or update the `google_vertex_ai_reasoning_engine` resource in `us-central1`.
  - `test_bqca_a2a/update_agent_cloudbuild.yaml` & `update_agent_deployment.sh`: Builds a new container image and directly patches an existing Reasoning Engine deployment via REST API.

---

## 📋 Prerequisites & Required IAM Permissions

### 1. Enable Required Google Cloud APIs
Ensure the following APIs are enabled in your host project (e.g., `<YOUR_PROJECT_ID>`):
```bash
gcloud services enable \
    geminidataanalytics.googleapis.com \
    cloudaicompanion.googleapis.com \
    agentregistry.googleapis.com \
    aiplatform.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    bigquery.googleapis.com \
    iamcredentials.googleapis.com
```

### 2. Required IAM Permissions Table
Because the Reasoning Engine service agent (`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) invokes Gemini Data Analytics (`geminidataanalytics.googleapis.com`) on behalf of the user, and Gemini Data Analytics queries BigQuery tables using the caller's identity, IAM roles must be granted across both the **host project** and any **external projects hosting BigQuery tables** referenced in `agent_card.json`.

| Principal (Service Account / Identity) | Required IAM Role | Target Project / Resource | Purpose |
| :--- | :--- | :--- | :--- |
| **Reasoning Engine Service Agent**<br>`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `roles/geminidataanalytics.admin`<br>*(or `roles/geminidataanalytics.dataAgentUser`)* | Host Project<br>*(e.g., `<YOUR_PROJECT_ID>`)* | Grants `geminidataanalytics.dataAgents.chat` permission to invoke `a2a.v1.A2AService.SendMessage` on the Data Agent. |
| **Reasoning Engine Service Agent**<br>`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `roles/cloudaicompanion.user` | Host Project<br>*(e.g., `<YOUR_PROJECT_ID>`)* | **Critical:** Required for `DataChatService.ChatInternal` inside Gemini Data Analytics when processing natural language queries. Without this, A2A calls fail with `403 Forbidden`. |
| **Reasoning Engine Service Agent**<br>`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `roles/serviceusage.serviceUsageConsumer` | Host Project<br>*(e.g., `<YOUR_PROJECT_ID>`)* | Allows the Google-managed Reasoning Engine service agent (`@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) to bill API quota to the host project via the `x-goog-user-project` header. |
| **Reasoning Engine Service Agent**<br>`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `roles/agentregistry.admin`<br>*(or `roles/agentregistry.viewer`)* | Host Project<br>*(e.g., `<YOUR_PROJECT_ID>`)* | Allows `AgentRegistry.get_remote_a2a_agent()` to fetch the registered A2A Agent Card during container startup. |
| **Reasoning Engine Service Agent**<br>`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `roles/aiplatform.user` | Host Project<br>*(e.g., `<YOUR_PROJECT_ID>`)* | Allows the orchestrator agent (`root_agent`) to invoke Vertex AI Gemini models (`gemini-3.1-flash-lite`, etc.). |
| **Reasoning Engine Service Agent**<br>`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `roles/artifactregistry.reader` | Host Project<br>*(e.g., `<YOUR_PROJECT_ID>`)* | Allows Vertex AI Reasoning Engine to pull the BYOC container image from Artifact Registry. |
| **Reasoning Engine Service Agent**<br>`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `roles/bigquery.jobUser`<br>`roles/bigquery.dataViewer` | Host Project<br>*(e.g., `<YOUR_PROJECT_ID>`)* | Allows Gemini Data Analytics to run BigQuery SQL jobs and read tables in the host project on behalf of the Reasoning Engine. |
| **Reasoning Engine Service Agent**<br>`service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `roles/bigquery.dataViewer` | **All External BigQuery Table Projects**<br>*(e.g., `base-tables`, `shared-data-project-493303`, `team-jj-493303`)* | **Critical:** Allows Gemini Data Analytics to retrieve table metadata and query tables located in external projects referenced in `agent_card.json`. |
| **Cloud Build / Deployment SA**<br>`<PROJECT_NUMBER>@cloudbuild.gserviceaccount.com` & `<PROJECT_NUMBER>-compute@developer.gserviceaccount.com` | `roles/geminidataanalytics.admin`<br>`roles/agentregistry.admin`<br>`roles/aiplatform.admin`<br>`roles/artifactregistry.writer`<br>`roles/storage.admin` | Host Project<br>*(e.g., `<YOUR_PROJECT_ID>`)* | Allows CI/CD pipelines to create/update Data Agents, register services in Agent Registry, push Docker images, and deploy Reasoning Engines via Terraform. |

#### Quick CLI Commands to Grant Reasoning Engine Permissions
```bash
PROJECT_ID="<YOUR_PROJECT_ID>"
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
RE_SA="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

# 1. Grant host project roles to Reasoning Engine Service Agent
for ROLE in \
    roles/geminidataanalytics.admin \
    roles/cloudaicompanion.user \
    roles/serviceusage.serviceUsageConsumer \
    roles/agentregistry.admin \
    roles/aiplatform.user \
    roles/artifactregistry.reader \
    roles/bigquery.jobUser \
    roles/bigquery.dataViewer; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
      --member="$RE_SA" \
      --role="$ROLE" \
      --condition=None
done

# 2. Grant BigQuery Data Viewer across external projects containing referenced tables
for EXT_PROJECT in base-tables shared-data-project-493303 team-jj-493303; do
  gcloud projects add-iam-policy-binding $EXT_PROJECT \
      --member="$RE_SA" \
      --role="roles/bigquery.dataViewer" \
      --condition=None
done
```

---

## 🛠️ Troubleshooting Common Errors

Below is a reference of common errors encountered when deploying BigQuery Conversational Analytics agents, Agent Registry services, and Vertex AI Reasoning Engine A2A orchestrators, along with their root causes and fixes:

| Error Message | Root Cause | Resolution |
| :--- | :--- | :--- |
| **`409 Resource 'projects/.../locations/global/dataAgents/<id>' already exists`** *(even after deleting the agent in the UI or via API)* | BigQuery Conversational Analytics **soft-deletes** `DataAgent` resources and retains the ID for **30 days** before permanent purge. Calling `create_data_agent()` with the same `data_agent_id` returns `409 AlreadyExists`. | Catch `google.api_core.exceptions.AlreadyExists` and call `update_data_agent()` with an explicit `FieldMask` (`paths=["display_name", "description", "data_analytics_agent"]`). Implemented in `data_agent_from_agent_card.py` and `data_agent_manual_creation.py`. |
| **`AttributeError: type object 'Exception' has no attribute 'AlreadyExists'`** | Python's built-in `Exception` class does not define gRPC/Google API status exceptions. | Import `from google.api_core import exceptions` and catch `exceptions.AlreadyExists`. |
| **`Client error '404 Not Found' for url '.../agents/<id>/.well-known/agent-card.json'`** | BigQuery Conversational Analytics does not serve its A2A card at `/.well-known/agent-card.json` under `/agents/`. | Fetch the live A2A Agent Card from the `/v1/a2a/.../v1/card` endpoint:<br>`https://geminidataanalytics.googleapis.com/v1/a2a/projects/{project_id}/locations/{location}/dataAgents/{data_agent_id}/v1/card` |
| **Agent registration script returns `200 OK`, but agent does not appear in Google Cloud Agent Registry UI** | 1. `POST /services` returns a **Long-Running Operation (LRO)**; if the LRO fails asynchronously (e.g., due to a duplicate interface URL already owned by another service ID), the service is not created.<br>2. Missing required fields (`name`, `description`, `version`, `url`) in `agentSpec.content`. | Check existing services in both `global` and `us-central1` for matching `url` or `serviceId`. If found, update via `PATCH ?updateMask=agentSpec,displayName,description` instead of `POST`, and poll the LRO (`/v1/{operation_name}`) until `"done": true`. Implemented in `data_agent_from_agent_card.py`. |
| **`The requested URL /v1/projects/<project>/locations/global/reasoningEngines was not found on this server`** *(during Terraform apply)* | Vertex AI Reasoning Engine (`aiplatform.googleapis.com`) is a **regional** resource and does not support `location = "global"`. | Set `location = "us-central1"` (or another supported region) in `terraform/variables.tf` and `test_bqca_a2a/cloudbuild.yaml`, while keeping `AGENT_REGISTRY_LOCATION = "global"` in `config.json`. |
| **`Error waiting to create ReasoningEngine: Error code 3, message: Reasoning Engine resource [...] failed to start and cannot serve traffic`** | The container crashed during startup before FastAPI could bind to port `8080`. Common causes:<br>1. Missing `google-adk[a2a,agent-identity]` or `mcp` packages.<br>2. `AgentRegistry.get_remote_a2a_agent()` failed with `403 PERMISSION_DENIED` because the Reasoning Engine service agent lacked `roles/agentregistry.admin`. | 1. Include `google-adk[a2a,agent-identity]` and `mcp` in `test_bqca_a2a/requirements.txt`.<br>2. Grant `roles/agentregistry.admin` on the host project to `service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`. |
| **`Failed to initialize remote A2A agent: Failed to initialize remote A2A agent test_no_code_agent: no compatible transports found`** | Gemini Data Analytics returns `"preferredTransport": "HTTP_JSON"` (with an underscore `_`) in its A2A Agent Card, whereas `a2a-sdk` 1.x (`TransportProtocol.HTTP_JSON`) expects `"HTTP+JSON"` (with a plus `+`). | 1. Normalize `agent_card_json_data["preferredTransport"] = "HTTP+JSON"` in `data_agent_from_agent_card.py` before registering in Agent Registry.<br>2. In `AuthRemoteA2aAgent._ensure_resolved` (`test_bqca_a2a/agent.py`), normalize `iface.protocol_binding = "HTTP+JSON"` on `self._agent_card.supported_interfaces`. |
| **`google.protobuf.json_format.ParseError: Failed to parse {'task': ...} field: unhashable type: 'dict'`** *(when sending A2A messages)* | In `google-adk` (`_compat.make_client_config`), `streaming` defaults to `True` on `a2a-sdk` 1.x, causing `a2a/compat/v0_3/rest_transport.py` to call `/v1/message:stream` and fail when parsing SSE task chunks. | Configure `A2AClientFactory` with `_compat.make_client_config(httpx_client=self._httpx_client, streaming=False, polling=False)` inside `AuthRemoteA2aAgent._ensure_httpx_client` (`test_bqca_a2a/agent.py`) so it calls `/v1/message:send` (non-streaming). |
| **`A2A request failed: HTTP Error 403: Client error '403 Forbidden' for url 'https://geminidataanalytics.googleapis.com/v1beta/a2a/projects/.../dataAgents/test_no_code_agent/v1/message:send'`** | When `service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` calls `SendMessage`, Gemini Data Analytics invokes `DataChatService.ChatInternal` and queries BigQuery using the caller's identity. The `403` occurs if the service agent is missing:<br>1. `roles/cloudaicompanion.user` or `roles/serviceusage.serviceUsageConsumer` on the host project.<br>2. `roles/bigquery.jobUser` on the host project.<br>3. `roles/bigquery.dataViewer` on **any external project** hosting BigQuery tables referenced by the agent.<br>4. The `x-goog-user-project` header on outbound A2A requests. | 1. Grant `roles/cloudaicompanion.user`, `roles/serviceusage.serviceUsageConsumer`, `roles/bigquery.jobUser`, and `roles/bigquery.dataViewer` on the host project to `service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`.<br>2. Grant `roles/bigquery.dataViewer` to `service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` on all external projects hosting referenced BigQuery tables.<br>3. Set `request.headers["x-goog-user-project"] = PROJECT_ID` in `GoogleCloudAuth.auth_flow` (`test_bqca_a2a/agent.py`). |

---

## 🛠️ Usage (Deploying via Cloud Build Triggers)

Deployments are automated by connecting your GitHub repository to **Google Cloud Build** and configuring Cloud Build Triggers pointing to the respective `cloudbuild.yaml` files in this repository.

### 1. Recommended Cloud Build Triggers

Create the following triggers in the Google Cloud Console under **Cloud Build > Triggers**:

| Example Trigger Name | Cloud Build Config File Location | Required User-Defined Substitutions | Description |
| :--- | :--- | :--- | :--- |
| **`deploy-bq-data-agent`** | `/cloudbuild.yaml` | *None*<br>*(Uses built-in `$PROJECT_ID`)* | Runs `data_agent_manual_creation.py` and `data_agent_from_agent_card.py` to create/update the BigQuery Conversational Analytics Data Agent and register its live A2A card in Google Cloud Agent Registry (`global` and `us-central1`). |
| **`deploy-bqca-a2a-reasoning-engine`** | `/test_bqca_a2a/cloudbuild.yaml` | **`_PROJECT_ID`**: `<your-project-id>` *(e.g., `<YOUR_PROJECT_ID>`)*<br>**`_LOCATION`**: `<region>` *(e.g., `us-central1`)* | Builds the ADK orchestrator container image (`test_bqca_a2a`), pushes it to Artifact Registry (`us-docker.pkg.dev/${PROJECT_ID}/agent-repo/test_bqca_a2a:${SHORT_SHA}`), and runs Terraform (`terraform/`) to provision or update the Vertex AI Reasoning Engine resource. |
| **`update-bqca-a2a-reasoning-engine`** | `/test_bqca_a2a/update_agent_cloudbuild.yaml` | *None*<br>*(Uses built-in `$PROJECT_ID` and `$SHORT_SHA`)* | Fast container-only update pipeline: builds and pushes a new container image for `test_bqca_a2a` and runs `update_agent_deployment.sh` to directly `PATCH` the existing Reasoning Engine deployment without running Terraform. |

### 2. Configuring Substitution Variables

When configuring the **`deploy-bqca-a2a-reasoning-engine`** trigger (pointing to `/test_bqca_a2a/cloudbuild.yaml`), ensure you add the following **Substitution Variables** in the Cloud Build Trigger configuration UI:

- **`_PROJECT_ID`**: Your target Google Cloud project ID (e.g., `<YOUR_PROJECT_ID>`). Passed to Terraform (`-var=project_id=${_PROJECT_ID}`).
- **`_LOCATION`**: The target regional location for the Vertex AI Reasoning Engine deployment (e.g., `us-central1`). Passed to Terraform (`-var=location=${_LOCATION}`). *(Note: Vertex AI Reasoning Engine requires a regional location such as `us-central1` and does not support `global`.)*

