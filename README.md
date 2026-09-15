# BigQuery Data Agent CI/CD Deployment

This repository provides CI/CD pipelines and Python scripts for automatically provisioning and configuring **Gemini Data Agents** (Conversational Analytics Agents) on Google Cloud using Cloud Build and the `google-cloud-geminidataanalytics` SDK. The sample code contains two use cases: 1) a low-code agent deployment example (agents are built out using natural language prompts and SQL, but wrapped and deployed with python SDK) and 2) a no-code agent deployment example (agent is created entirely in BigQuery, and the auto-generated A2A card is then used to extract and re-create the agent in other projects. 

The purpose of this code is to present a way to promote low/no code data agents across environments (dev/stage/prod) in an automated fashion and integrate wtih CI/CD pipelines. 

---

## 📁 Repository Structure

```
.
├── agent_card.json                 # A2A Agent Card specification containing agent metadata, table references, and example queries
├── cloudbuild.yaml                 # Cloud Build pipeline configuration for automated agent provisioning
├── data_agent_from_agent_card.py   # Script that dynamically parses an agent card JSON and deploys the Data Agent
├── data_agent_manual_creation.py   # Example script for defining and deploying a Data Agent programmatically
└── requirements.txt                # Python dependencies for deployment
```

---

## 🚀 Key Components

### 1. `data_agent_from_agent_card.py` (no-code deployment)
Dynamically creates a Data Agent from an Agent Card JSON file (`agent_card.json`):
- **Agent Name & ID**: Extracts agent name, creates a CI/CD-specific ID (e.g., `<name>_cicd`).
- **System Instructions**: Extracts system prompt instructions from `BQ Dataset Information` extension.
- **Datasource References**: Parses fully-qualified BigQuery table strings (`project.dataset.table`) into dynamic `BigQueryTableReference` objects.
- **Golden Example Queries**: Parses natural language questions and corresponding SQL queries into `ExampleQuery` protobuf definitions.
- **Data Agent Deployment**: Registers the agent with `geminidataanalytics.DataAgentServiceClient`.

### 2. `data_agent_manual_creation.py` (low-code deployment)
A reference script demonstrating how to hardcode and deploy a custom Data Agent (e.g. Google Trends analytical agent) with join instructions, column guidelines, and golden queries.

### 3. `cloudbuild.yaml`
Automated CI/CD build configuration:
- Runs in a lightweight `python:3.11-slim` container.
- Automatically installs dependencies from `requirements.txt`.
- Deploys the Data Agent using the Cloud Build `$PROJECT_ID` substitution.

---

## 📋 Prerequisites & IAM Setup

### 1. Enable Required GCP APIs
Ensure the following APIs are enabled in your target Google Cloud project:
```bash
gcloud services enable \
    geminidataanalytics.googleapis.com \
    cloudbuild.googleapis.com \
    bigquery.googleapis.com
```

### 2. Required IAM Permissions
The identity executing the deployment (or the Cloud Build Service Account: `<project-number>@cloudbuild.gserviceaccount.com`) requires:
- **Gemini Data Analytics Admin**: `roles/geminidataanalytics.admin`
- **BigQuery Data Viewer**: `roles/bigquery.dataViewer` (or access to referenced datasets/tables in their respective projects)
- **BigQuery Job User**: `roles/bigquery.jobUser` (in the project where the data agent is deployed)

---

## 🛠️ Usage

### Local / Manual Execution

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Authenticate with GCP:**
   ```bash
   gcloud auth application-default login
   ```

3. **Run the script:**
   ```bash
   python3 data_agent_manual_creation.py
   ```

---

### CI/CD Deployment via Cloud Build
Connect this repository to Cloud Build to automatically trigger agent deployment on push to `main`.
