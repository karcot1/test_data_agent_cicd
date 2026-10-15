# BigQuery Conversational Analytics Data Agent CI/CD

This repository deploys **BigQuery Conversational Analytics (Gemini Data Analytics) Data Agents** from definitions kept in Git, and registers them in **Google Cloud Agent Registry** so other agents and **Gemini Enterprise** can discover them over the **Agent-to-Agent (A2A) protocol**.

A single deployer (`python -m deployer`) reads every agent definition in the repo. It creates or updates **only** the Data Agents whose definitions changed, and syncs no-code agents to Agent Registry. Agents can be defined two ways:
- **No-code**: a declarative JSON config exported from a published BigQuery agent, saved in `agent_configs/`.
- **Low-code**: a small Python module in `low_code_agents/` that builds the `DataAgent` with the `google-cloud-geminidataanalytics` SDK.

Two helper commands are run by hand rather than in the pipeline:
- `python -m deployer.export_agent` saves a published agent as a JSON config in `agent_configs/`.
- `python -m deployer.ge_egress` grants Gemini Enterprise agent identities the IAP egress roles they need to call your Data Agents.

---

## 📁 Repository Structure

```text
.
├── agent_configs/                         # No-code agents: one JSON config per agent
│   ├── _template.json                     # Annotated template (files starting with _ are not deployed)
│   └── thelook_ecommerce_agent.json       # Sample agent on the public theLook eCommerce dataset
├── low_code_agents/                       # Low-code agents: one Python module per agent (AGENT_ID + build_agent)
├── deployer/                              # Deployer package
│   ├── __main__.py / cli.py               # python -m deployer: load → reconcile → Agent Registry → summary
│   ├── specs.py                           # Loads and validates agent configs / low-code modules into AgentSpecs
│   ├── reconcile.py                       # get → diff → CREATE / MODIFY / SKIP
│   ├── registry.py                        # Agent Registry sync (locations configurable, default: global)
│   ├── export_agent.py                    # python -m deployer.export_agent: save a published agent as a config
│   ├── endpoints.py                       # Global vs. regional Gemini Data Analytics endpoints
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
├── CONTRIBUTING.md
├── LICENSE
├── .dockerignore
└── .gitignore
```

---

## 🚀 How It Works

### The deployer (`python -m deployer`)

```bash
python -m deployer --project_id=<PROJECT_ID> [--dry-run] [--skip-registry]
                   [--registry_locations global [us-central1 ...]]
                   [--config_dir=agent_configs] [--low_code_dir=low_code_agents] [--location=global]
```

For every agent definition, the deployer:
- **Builds the desired `DataAgent`.**
  - From a JSON config (see [Agent config schema](#agent-config-schema)): system instruction, tables, example queries, glossary terms and options. Tables, example SQL and the system instruction that point to `source_project_id` are rewritten to `--project_id`, so the same config promotes from DEV to STG and PRD.
  - From a low-code module, `build_agent()` returns the `DataAgent` directly.
- **Diffs it against the live agent** (`display_name`, `description`, `system_instruction`, `table_references`, `example_queries`, `glossary_terms`, `python_enabled`, soft-delete state) and prints one of:
  - `[CREATE]`: the agent doesn't exist. An ID that was soft-deleted (`409 AlreadyExists`, 30-day retention) is restored in place with `update_data_agent()`.
  - `[MODIFY]`: one or more fields changed. The changed fields are listed.
  - `[SKIP]`: already up to date. No API writes.
- **Syncs Agent Registry** (no-code agents only). This runs for each location in `--registry_locations` (default: `global`), when the agent was created or modified, or is missing from a location. It:
  1. fetches the live A2A card from `.../dataAgents/{id}/v1/card` (on the regional endpoint for non-global agents)
  2. normalizes `preferredTransport` to `HTTP+JSON` for `a2a-sdk`
  3. `PATCH`es or `POST`s the service, then polls the operation (60-second timeout)

A failure on one agent doesn't stop the others. The run prints a summary and exits non-zero if anything failed. Invalid definitions stop the run with exit code `2` before anything is changed. That covers bad JSON, unknown or misspelled keys, an invalid `agent_id`, duplicate IDs, and a config with no tables.

> [!NOTE]
> Agent Registry entries are stored per location and are **not** replicated between locations. Publish to more than `global` only if a consumer reads from another location, for example `_DEPLOY_ARGS="--registry_locations global us-central1"` on the Cloud Build trigger. Entries that already exist in other locations are left in place; delete them manually if they're no longer needed.

### Agent config schema

Each file in `agent_configs/` describes one Data Agent with the fields the Conversational Analytics API expects ([`_template.json`](agent_configs/_template.json)):

| Field | Required | Maps to |
| :--- | :---: | :--- |
| `agent_id` | Yes | Data Agent ID. Starts with a lowercase letter; lowercase letters, digits, `_` or `-`; max 63 characters. |
| `location` | No | Data Agent location (`global`, `us`, `eu`, etc.). Preserved by `export_agent`; defaults to the CLI `--location` (`global`) when omitted. |
| `display_name`, `description` | No | `DataAgent.display_name`, `DataAgent.description` |
| `source_project_id` | No | Project the agent was exported from. References to it are rewritten to the target project; other projects (e.g. `bigquery-public-data`) are left as-is. |
| `system_instruction` | No | `Context.system_instruction` |
| `table_references` | Yes (≥ 1) | `Context.datasource_references.bq.table_references` (`project_id`, `dataset_id`, `table_id`) |
| `example_queries` | No | `Context.example_queries` (`natural_language_question`, `sql_query`, optional `parameters` with `name`, `description`, `data_type`) |
| `glossary_terms` | No | `Context.glossary_terms` (`display_name`, `description`, `labels`) |
| `options.python_enabled` | No | `Context.options.analysis.python.enabled` (default `false`) |

Unknown keys are rejected, so typos fail the build instead of being silently ignored.

> [!NOTE]
> The deployer assumes dataset and table names are the same in every environment. The Conversational Analytics API checks that every referenced table exists in the target project, even if it's empty.

Limitations:
- `source_project_id` rewriting only matches the `project.dataset.table` form. References written as `` `project`.dataset.table `` or `project:dataset.table` are left unchanged.
- The schema doesn't cover table and column descriptions or schema relationships. `deployer.export_agent` doesn't export them, and the deployer doesn't set them.

> [!IMPORTANT]
> Treat Git as the single source of truth in pipeline-managed environments (STG, PRD). A deployment replaces the agent's **published** context with the one in the repository, so changes published directly in BigQuery Studio there are overwritten. Drafts (unpublished edits) are left untouched. Make changes in DEV, export them, and promote them through a pull request.

### Adding an agent
- **No-code:** publish the agent in BigQuery Studio, then export it:
  ```bash
  .venv/bin/python -m deployer.export_agent \
      --source projects/<SOURCE_PROJECT>/locations/<LOCATION>/dataAgents/<AGENT_ID> \
      --out agent_configs/<anything>.json [--agent_id my_agent]
  ```
  `--source` also accepts the agent card's `url`. By default the agent deploys under an ID derived from its display name (`"My Sales Agent"` → `my_sales_agent`); use `--agent_id` to choose another. The exporter reads the Data Agent resource (`GetDataAgent`), keeps only the published context and drops server-populated fields such as table schemas.
  > **Why not the A2A agent card?** A2A agent cards (from the BigQuery UI or `.../v1/card`) describe how to *call* an agent. They no longer include its tables, instructions or example queries, so they can't be used to recreate it. The deployer still uses the live card to register the agent in Agent Registry.
- You can also write or edit a config by hand, starting from [`_template.json`](agent_configs/_template.json).
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
  Leave `name` unset; the deployer fills it in from `AGENT_ID`, which follows the same rules as `agent_id`. Files starting with `_` are ignored, so you can use them for shared helpers.

### Gemini Enterprise egress IAM (manual)
This step is **not** in the pipeline. Run it only after you've added one or more Data Agents to a Gemini Enterprise app. Without it, GE calls to the agent fail with `403: Egress request is not authorized`.

```bash
.venv/bin/python -m deployer.ge_egress --project_id=<PROJECT_ID> --dry-run    # show missing bindings
.venv/bin/python -m deployer.ge_egress --project_id=<PROJECT_ID>              # apply
.venv/bin/python -m deployer.ge_egress --project_id=<PROJECT_ID> --agent_ids thelook_ecommerce_agent
```

What it does:
1. Lists every Gemini Enterprise app in the project (location `global`, collection `default_collection`).
2. In each app's `default_assistant`, finds the agents that front your Data Agents. By default it looks for every agent defined in the repo; `--agent_ids` narrows that.
3. Adds those agents' principals to:
   - `roles/iap.viewer` and `roles/iap.egressor` on the project
   - `roles/iap.egressor` on `iap_web`

It only adds members and never removes any.

### Testing an agent manually
[examples/deploy_a2a.py](examples/deploy_a2a.py) fetches a Data Agent's A2A card and sends it one message over A2A:
```bash
pip install -r examples/requirements.txt
PROJECT_ID=<PROJECT_ID> AGENT_ID=thelook_ecommerce_agent python examples/deploy_a2a.py
```
Set `LOCATION` too if the agent isn't in `global`.

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
| | `roles/bigquery.dataViewer` | Datasets the agents reference | The API checks that every referenced table exists when it creates or updates an agent. |
| **Person running `deployer.export_agent`** | `roles/geminidataanalytics.dataAgentViewer` | Project that owns the source agent | Read the Data Agent definition from the API. |
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
| **Deployer stops with `Invalid agent definition: ...`** | The config has invalid JSON, an unknown key, an invalid `agent_id`, or no `table_references`. | Fix the field named in the message. Compare with [`agent_configs/_template.json`](agent_configs/_template.json). |
| **`Exported agent is not deployable: ... has no published BigQuery table references`** | The agent was never published, or it uses a data source other than BigQuery tables. | Publish the agent in BigQuery Studio and export it again. |
| **`403: Read access to project ... was denied`** for an agent in `us` or `eu` | Agents outside `global` are only served by their regional endpoint (e.g. `geminidataanalytics.us.rep.googleapis.com`). | Handled in `deployer/endpoints.py`. Pass `--location` to the deployer and the agent's full resource name to `deployer.export_agent`. |
| **`[CREATE]` or `[MODIFY]` fails with `403`/`404` on a table** | A referenced table doesn't exist in the target project, or the Cloud Build service account can't read it. | Create the same datasets and tables in every environment, and grant `roles/bigquery.dataViewer` on them. |
| **`403: Egress request is not authorized`** when using the agent from Gemini Enterprise | The GE agent's identity is missing the IAP egress permissions checked by the agent gateway (`iap.resources.egressViaIAP`, part of `roles/iap.viewer`). | Run `python -m deployer.ge_egress --project_id=<PROJECT_ID>` after adding the agent to the GE app. |
| **`409 Resource 'projects/.../locations/global/dataAgents/<id>' already exists`** *(even after deleting the agent)* | Gemini Data Analytics **soft-deletes** `DataAgent` resources and keeps the ID for **30 days**. | Handled automatically: on `AlreadyExists` the deployer calls `update_data_agent()` instead (`deployer/reconcile.py`). |
| **Agent registration returns `200 OK`, but the agent doesn't appear in the Agent Registry UI** | `POST /services` returns a long-running operation that can fail later, e.g. because another service already owns the same interface URL. Missing `name` / `description` / `version` / `url` in `agentSpec.content` also causes failures. | Handled in `deployer/registry.py`: it matches existing services by `url` or `serviceId`, `PATCH`es them instead of creating duplicates, fills required card fields, and polls the operation until it finishes. |
| **`400 Bad Request` from `agentregistry.googleapis.com/.../services?serviceId=...`** | Agent Registry limits `serviceId` and `displayName` to **63 characters**. | Handled in `deployer/registry.py`, which truncates both to 63 characters. |
| **`no compatible transports found`** when an `a2a-sdk` client loads the registered card | Gemini Data Analytics returns `"preferredTransport": "HTTP_JSON"`, but `a2a-sdk` 1.x expects `"HTTP+JSON"`. | Handled in `deployer/registry.py`, which normalizes `preferredTransport` to `HTTP+JSON` before registering. |
| **`403 Forbidden` on `.../dataAgents/<id>/v1/message:send`** | The calling identity lacks permission to chat with the agent or to query its tables. | Grant the caller the roles in the "Anyone chatting with a Data Agent" rows above, including `roles/bigquery.dataViewer` on every project that owns a referenced table. Send the `x-goog-user-project` header on A2A requests. |
| **Cloud Build `deploy` step fails with `403` from `discoveryengine.googleapis.com`** | An older pipeline version ran the GE egress step in Cloud Build. | Update to the current `cloudbuild.yaml`. The egress step is now manual (`deployer.ge_egress`). |

---

## License

This project is licensed under the [Apache License 2.0](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) to contribute.

This is not an officially supported Google product. This project is not eligible for the [Google Open Source Software Vulnerability Rewards Program](https://bughunters.google.com/open-source-security).
