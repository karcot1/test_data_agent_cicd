# BigQuery Conversational Analytics Data Agent CI/CD

This repository deploys **BigQuery Conversational Analytics (Gemini Data Analytics) Data Agents** from definitions kept in Git, and registers them in **Google Cloud Agent Registry** so other agents and **Gemini Enterprise** can discover them over the **Agent-to-Agent (A2A) protocol**.

A single deployer (`python -m deployer`) reads every agent definition in the repo. It creates or updates **only** the Data Agents whose definitions changed, and syncs no-code agents to Agent Registry. Agents can be defined two ways:
- **No-code**: an A2A Agent Card exported from a published BigQuery agent, saved in `agent_cards/`.
- **Low-code**: a small Python module in `low_code_agents/` that builds the `DataAgent` with the `google-cloud-geminidataanalytics` SDK.

Two helper commands are run by hand rather than in the pipeline:
- `python -m deployer.export_card` saves a published agent's full A2A card into `agent_cards/`.
- `python -m deployer.ge_egress` grants Gemini Enterprise agent identities the IAP egress roles they need to call your Data Agents.

---

## 📁 Repository Structure

```text
.
├── agent_cards/                           # No-code agents: one A2A Agent Card JSON per agent
├── low_code_agents/                       # Low-code agents: one Python module per agent (AGENT_ID + build_agent)
├── deployer/                              # Deployer package
│   ├── __main__.py / cli.py               # python -m deployer: load → reconcile → Agent Registry → summary
│   ├── specs.py                           # Loads and validates agent cards / low-code modules into AgentSpecs
│   ├── reconcile.py                       # get → diff → CREATE / MODIFY / SKIP
│   ├── registry.py                        # Agent Registry sync (locations configurable, default: global)
│   ├── export_card.py                     # python -m deployer.export_card: save a full agent card from the API
│   ├── ge_egress.py                       # python -m deployer.ge_egress: manual Gemini Enterprise egress IAM
│   ├── iam.py                             # IAP egress binding logic used by ge_egress
│   ├── auth.py                            # ADC with gcloud fallback
│   └── Dockerfile                         # Dependencies-only image used by the Cloud Build steps
├── tests/                                 # Offline unit tests (no GCP calls)
├── examples/
│   ├── deploy_a2a.py                      # Standalone A2A client for manually testing a Data Agent endpoint
│   └── requirements.txt                   # Dependencies for the example (a2a-sdk, etc.)
├── cloudbuild.yaml                        # Build image → test → deploy
├── requirements.in                        # Top-level dependencies (edit this)
├── requirements.txt                       # Fully pinned lock file (generated from requirements.in)
├── pytest.ini                             # pytest configuration
├── .dockerignore
└── .gitignore
```

---

## 🚀 How It Works

### The deployer (`python -m deployer`)

```bash
python -m deployer --project_id=<PROJECT_ID> [--dry-run] [--skip-registry]
                   [--registry_locations global [us-central1 ...]]
                   [--no_code_dir=agent_cards] [--low_code_dir=low_code_agents] [--location=global]
```

For every agent definition, the deployer:
- **Builds the desired `DataAgent`.**
  - From an agent card, it reads the `BQ Dataset Information` extension:
    - system instructions
    - golden SQL example queries (`ExampleQuery`). Parameters can be given as names or as `{name, description, data_type}` objects.
    - fully-qualified table references (`project.dataset.table`)
  - From a low-code module, `build_agent()` returns the `DataAgent` directly.
- **Diffs it against the live agent** (`display_name`, `description`, `system_instruction`, `table_references`, `example_queries`, soft-delete state) and prints one of:
  - `[CREATE]`: the agent doesn't exist. An ID that was soft-deleted (`409 AlreadyExists`, 30-day retention) is restored in place with `update_data_agent()`.
  - `[MODIFY]`: one or more fields changed. The changed fields are listed.
  - `[SKIP]`: already up to date. No API writes.
- **Syncs Agent Registry** (no-code agents only). This runs for each location in `--registry_locations` (default: `global`), when the agent was created or modified, or is missing from a location. It:
  1. fetches the live card from `.../dataAgents/{id}/v1/card`
  2. normalizes `preferredTransport` to `HTTP+JSON` for `a2a-sdk` 1.x
  3. `PATCH`es or `POST`s the service, then polls the operation

A failure on one agent doesn't stop the others. The run prints a summary and exits non-zero if anything failed. Invalid definitions stop the run with exit code `2` before anything is changed. That covers bad JSON, a missing `name`, duplicate IDs, and a card with no `BQ Dataset Information` extension or no tables.

> [!NOTE]
> Agent Registry entries are stored per location and are **not** replicated between locations. Publish to more than `global` only if a consumer reads from another location, for example `_DEPLOY_ARGS="--registry_locations global us-central1"` on the Cloud Build trigger. Entries that already exist in other locations are left in place; delete them manually if they're no longer needed.

### Adding an agent
- **No-code:** publish the agent in BigQuery, then export its card from the API:
  ```bash
  .venv/bin/python -m deployer.export_card \
      --source projects/<SOURCE_PROJECT>/locations/global/dataAgents/<AGENT_ID> \
      --out agent_cards/<anything>.json
  ```
  `--source` also accepts the card's `url`. The Data Agent deploys as `<card name>_no_code_agent`.
  > **Don't copy the A2A JSON from the BigQuery UI.** That copy omits the `BQ Dataset Information` extension (tables, system instructions, example queries). The deployer rejects cards without it, so they can't create empty agents. The API card includes it.
- **Low-code:** add `low_code_agents/<anything>.py` defining:
  ```python
  from google.cloud import geminidataanalytics

  AGENT_ID = "my_low_code_agent"

  def build_agent(project_id: str, location: str) -> geminidataanalytics.DataAgent:
      return geminidataanalytics.DataAgent(
          data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
              published_context=geminidataanalytics.Context(...)
          )
      )
  ```
  Leave `name` unset; the deployer fills it in from `AGENT_ID`. Files starting with `_` are ignored, so you can use them for shared helpers.

### Gemini Enterprise egress IAM (manual)
This step is **not** in the pipeline. Run it only after you've added one or more Data Agents to a Gemini Enterprise app. Without it, GE calls to the agent fail with `403: Egress request is not authorized`.

```bash
.venv/bin/python -m deployer.ge_egress --project_id=<PROJECT_ID> --dry-run    # show missing bindings
.venv/bin/python -m deployer.ge_egress --project_id=<PROJECT_ID>              # apply
.venv/bin/python -m deployer.ge_egress --project_id=<PROJECT_ID> --agent_ids my_agent_no_code_agent
```

What it does:
1. Lists every Gemini Enterprise app in the project (location `global`, collection `default_collection`).
2. In each app's `default_assistant`, finds the agents that front your Data Agents. By default it looks for every agent defined in the repo; `--agent_ids` narrows that.
3. Adds those agents' principals to:
   - `roles/iap.viewer` and `roles/iap.egressor` on the project
   - `roles/iap.egressor` on `iap_web`

It only adds members and never removes any.

### Testing an agent manually
[examples/deploy_a2a.py](examples/deploy_a2a.py) fetches a Data Agent's A2A card and sends it one message over A2A. Set `AGENT_ID` in the script, then run:
```bash
pip install -r examples/requirements.txt
PROJECT_ID=<PROJECT_ID> python examples/deploy_a2a.py
```

### Running locally
```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m deployer --project_id=<PROJECT_ID> --dry-run
```
To change dependencies, edit `requirements.in`, then run `uv pip compile requirements.in -o requirements.txt --python-version 3.11`.

---

## 📋 Prerequisites & Required IAM Permissions

### 1. Enable Required Google Cloud APIs
```bash
gcloud services enable \
    geminidataanalytics.googleapis.com \
    cloudaicompanion.googleapis.com \
    agentregistry.googleapis.com \
    bigquery.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com
```
If you use Gemini Enterprise and `deployer.ge_egress`, also enable `discoveryengine.googleapis.com`.

The pipeline pushes its image to an Artifact Registry Docker repository named `agent-repo` in the `us` multi-region. Create it once if it doesn't exist:
```bash
gcloud artifacts repositories create agent-repo --repository-format=docker --location=us
```

### 2. Required IAM Permissions

| Who | Role(s) | Where | Why |
| :--- | :--- | :--- | :--- |
| **Cloud Build service account**<br>(the account the trigger runs as, e.g. `<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`) | `roles/geminidataanalytics.dataAgentEditor`<br>*(or `roles/geminidataanalytics.admin`)* | Deployment project | Create, read and update Data Agents. |
| | `roles/agentregistry.editor`<br>*(or `roles/agentregistry.admin`)* | Deployment project | List, create and update Agent Registry services. |
| | `roles/artifactregistry.writer` | `agent-repo` repository | Push the deployer image. |
| | `roles/logging.logWriter` | Deployment project | Write build logs (`CLOUD_LOGGING_ONLY`). |
| **Person running `deployer.export_card`** | `roles/geminidataanalytics.dataAgentViewer` | Project that owns the source agent | Read the full A2A card from the API. |
| **Person running `deployer.ge_egress`** | `roles/discoveryengine.viewer` | Deployment project | List Gemini Enterprise apps and agents. |
| | Permission to update IAM, e.g. `roles/iam.securityAdmin` | Deployment project | Add the IAP egress bindings on the project and `iap_web`. |
| **Anyone (or any agent identity) chatting with a Data Agent** | `roles/geminidataanalytics.dataAgentUser`, `roles/cloudaicompanion.user` | Deployment project | Chat with the Data Agent. |
| | `roles/bigquery.jobUser` | Deployment project | Run the SQL the agent generates. |
| | `roles/bigquery.dataViewer` | Every project that owns a table the agent references | Read table metadata and data. Gemini Data Analytics queries with the caller's identity. |

> [!CAUTION]
> Don't grant the Cloud Build service account broad IAM-admin roles (such as `roles/iam.securityAdmin`) just for this pipeline. The pipeline doesn't change IAM. The egress bindings are applied manually with `deployer.ge_egress`.

---

## 🛠️ Usage (Deploying via Cloud Build Triggers)

Connect the GitHub repository to **Google Cloud Build** and create triggers under **Cloud Build > Triggers** pointing at `/cloudbuild.yaml`. The pipeline:
1. pulls the previous `agent-deployer:latest` image to use as a layer cache
2. builds the dependencies-only image (`deployer/Dockerfile`)
3. pushes the image, and runs the unit tests at the same time
4. runs `python -m deployer --project_id=$PROJECT_ID ${_DEPLOY_ARGS}`

| Example Trigger Name | Config File | Substitutions | Description |
| :--- | :--- | :--- | :--- |
| **`deploy-bq-data-agent`** *(push to main)* | `/cloudbuild.yaml` | *None* (uses built-in `$PROJECT_ID`) | Creates/updates only the changed Data Agents and syncs Agent Registry (`global` by default). |
| **`plan-bq-data-agent`** *(pull requests)* | `/cloudbuild.yaml` | **`_DEPLOY_ARGS`**: `--dry-run` | Same pipeline in dry-run mode: reports planned `[CREATE]` / `[MODIFY]` / `[SKIP]` actions without changing anything. |

Other substitutions:
- **`_DEPLOY_ARGS`**: extra deployer flags, e.g. `--registry_locations global us-central1` or `--skip-registry`.
- **`_DEPLOYER_IMAGE`**: the image path. Default `us-docker.pkg.dev/${PROJECT_ID}/agent-repo/agent-deployer`.

To run the pipeline once without a trigger:
```bash
gcloud builds submit --config cloudbuild.yaml .
```

---

## 🛠️ Troubleshooting Common Errors

| Error / Symptom | Root Cause | Resolution |
| :--- | :--- | :--- |
| **Deployer stops with `Invalid agent definition: ... has no 'BQ Dataset Information' extension`** | The card was copied from the BigQuery UI, which omits the extension that carries tables, instructions and example queries. | Re-export the card with `python -m deployer.export_card --source <agent> --out agent_cards/<file>.json`. |
| **Agent is created but has no tables or instructions** | The card had no usable `BQ Dataset Information` extension. The deployer now rejects these cards; older runs could create empty agents. | Re-export the card with `deployer.export_card` and re-run the pipeline. The next run reports `[MODIFY]` and fills in the agent. |
| **`403: Egress request is not authorized`** when using the agent from Gemini Enterprise | The GE agent's identity is missing the IAP egress permissions checked by the agent gateway (`iap.resources.egressViaIAP`, part of `roles/iap.viewer`). | Run `python -m deployer.ge_egress --project_id=<PROJECT_ID>` after adding the agent to the GE app. |
| **`409 Resource 'projects/.../locations/global/dataAgents/<id>' already exists`** *(even after deleting the agent)* | Gemini Data Analytics **soft-deletes** `DataAgent` resources and keeps the ID for **30 days**. | Handled automatically: on `AlreadyExists` the deployer calls `update_data_agent()` instead (`deployer/reconcile.py`). |
| **Agent registration returns `200 OK`, but the agent doesn't appear in the Agent Registry UI** | `POST /services` returns a long-running operation that can fail later, e.g. because another service already owns the same interface URL. Missing `name` / `description` / `version` / `url` in `agentSpec.content` also causes failures. | Handled in `deployer/registry.py`: it matches existing services by `url` or `serviceId`, `PATCH`es them instead of creating duplicates, fills required card fields, and polls the operation until it finishes. |
| **`400 Bad Request` from `agentregistry.googleapis.com/.../services?serviceId=...`** | Agent Registry limits `serviceId` and `displayName` to **63 characters**. | Handled in `deployer/registry.py`, which truncates both to 63 characters. |
| **`no compatible transports found`** when an `a2a-sdk` client loads the registered card | Gemini Data Analytics returns `"preferredTransport": "HTTP_JSON"`, but `a2a-sdk` 1.x expects `"HTTP+JSON"`. | Handled in `deployer/registry.py`, which normalizes `preferredTransport` to `HTTP+JSON` before registering. |
| **`403 Forbidden` on `.../dataAgents/<id>/v1/message:send`** | The calling identity lacks permission to chat with the agent or to query its tables. | Grant the caller the roles in the "Anyone chatting with a Data Agent" rows above, including `roles/bigquery.dataViewer` on every project that owns a referenced table. Send the `x-goog-user-project` header on A2A requests. |
| **Cloud Build `deploy` step fails with `403` from `discoveryengine.googleapis.com`** | An older pipeline version ran the GE egress step in Cloud Build. | Update to the current `cloudbuild.yaml`. The egress step is now manual (`deployer.ge_egress`). |
