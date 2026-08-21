# Gemini Data Agent CI/CD Deployment

This repository provides CI/CD pipelines and Python scripts for automatically provisioning and configuring **Gemini Data Agents** (Conversational Analytics Agents) on Google Cloud using Cloud Build and the `google-cloud-geminidataanalytics` SDK.

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

### 1. `data_agent_from_agent_card.py`
Dynamically creates a Data Agent from an Agent Card JSON file (`agent_card.json`):
- **Agent Name & ID**: Extracts agent name, creates a CI/CD-specific ID (e.g., `<name>_cicd`).
- **System Instructions**: Extracts system prompt instructions from `BQ Dataset Information` extension.
- **Datasource References**: Parses fully-qualified BigQuery table strings (`project.dataset.table`) into dynamic `BigQueryTableReference` objects.
- **Golden Example Queries**: Parses natural language questions and corresponding SQL queries into `ExampleQuery` protobuf definitions.
- **Data Agent Deployment**: Registers the agent with `geminidataanalytics.DataAgentServiceClient`.

### 2. `data_agent_manual_creation.py`
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