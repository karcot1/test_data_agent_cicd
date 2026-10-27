# BigQuery Conversational Analytics Data Agent CI/CD

This repository deploys **BigQuery Conversational Analytics (Gemini Data Analytics) Data Agents** from definitions kept in Git, and registers them in **Google Cloud Agent Registry** so other agents and **Gemini Enterprise** can discover them over the **Agent-to-Agent (A2A) protocol**.

A single deployer (`python -m deployer`) reads every agent definition in the repo. It creates or updates **only** the Data Agents whose definitions changed, and syncs no-code agents to Agent Registry. Agents can be defined two ways:
- **No-code**: a declarative JSON config exported from a published BigQuery agent, saved in `agent_configs/`.
- **Low-code**: a small Python module in `low_code_agents/` that builds the `DataAgent` with the `google-cloud-geminidataanalytics` SDK.

Two commands are run by hand rather than in the pipeline:
- `python -m deployer.export_agent` saves a published agent as a JSON config in `agent_configs/`. It's part of the core workflow.
- `python -m deployer.ge_egress` is an optional helper for connecting agents to a Gemini Enterprise app. It grants the GE agent identities the IAP egress roles they need to call your Data Agents. It isn't part of the core deployer and isn't covered by the test suite; see [Gemini Enterprise egress IAM (optional helper)](#gemini-enterprise-egress-iam-optional-helper).

This repository accompanies the Google Cloud Blog post [AgentOps for Conversational Analytics in BigQuery](<BLOG_POST_URL>).

---

## 📁 Repository Structure

```text
.
├── agent_configs/                         # No-code agents: one JSON config per agent
│   ├── _template.json                     # Annotated template (files starting with _ are not deployed)
│   └── thelook_ecommerce_agent.json       # Sample agent on the public theLook eCommerce dataset
├── low_code_agents/                       # Low-code agents: one Python module per agent (AGENT_ID + build_agent)
│   └── google_trends_data_agent.py        # Sample agent on the public Google Trends dataset
├── deployer/                              # Deployer package
│   ├── __main__.py / cli.py               # python -m deployer: load → reconcile → Agent Registry → summary
│   ├── specs.py                           # Loads and validates agent configs / low-code modules into AgentSpecs
│   ├── reconcile.py                       # get → diff → CREATE / MODIFY / SKIP
│   ├── registry.py                        # Agent Registry sync (locations configurable, default: global)
│   ├── export_agent.py                    # python -m deployer.export_agent: save a published agent as a config
│   ├── endpoints.py                       # Global vs. regional Gemini Data Analytics endpoints
│   ├── ge_egress.py                       # Optional GE helper (not core, not tested end to end): python -m deployer.ge_egress
│   ├── iam.py                             # IAP egress binding logic used by ge_egress
│   ├── auth.py                            # ADC with gcloud fallback
│   └── Dockerfile                         # Dependencies-only image used by the Cloud Build steps
├── tests/                                 # Offline tests for the core deployer (no GCP calls or credentials needed)
├── examples/
│   ├── deploy_a2a.py                      # Standalone A2A client for manually testing a Data Agent endpoint
│   └── requirements.txt                   # Dependencies for the example (a2a-sdk, etc.)
├── cloudbuild.yaml                        # Build image → test → deploy
├── requirements.in                        # Top-level dependencies (edit this)
├── requirements.txt                       # Fully pinned lock file (generated from requirements.in)
├── pytest.ini                             # pytest configuration
├── IAM_Mapping.md                         # IAM permissions and suggested roles per persona
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
- **Diffs it against the live agent.** Every field the deployer owns is compared: `display_name`, `description`, `labels`, the whole published context (including settings the config format can't express, such as table/column schema metadata, schema relationships and user functions), the BigQuery Studio draft (`staging_context`) and soft-delete state. Only server-managed fields (timestamps, `last_published_context`, `kms_key`) are ignored. It then prints one of:
  - `[CREATE]`: the agent doesn't exist. An ID that was soft-deleted (`409 AlreadyExists`, 30-day retention) is restored in place with `update_data_agent()`.
  - `[MODIFY]`: one or more fields changed. The changed fields are listed.
  - `[SKIP]`: already up to date. No API writes.
- **Syncs Agent Registry** (no-code agents only). This runs for each location in `--registry_locations` (default: `global`), when the agent was created or modified, or is missing from a location. It:
  1. fetches the live A2A card from `.../dataAgents/{id}/v1/card` (on the regional endpoint for non-global agents)
  2. normalizes `preferredTransport` to `HTTP+JSON` for `a2a-sdk`
  3. `PATCH`es or `POST`s the service, then polls the operation (60-second timeout)

A failure on one agent doesn't stop the others. The run prints a summary and exits non-zero if anything failed. Invalid definitions stop the run with exit code `2` before anything is changed. That covers bad JSON, unknown or misspelled keys, an invalid `agent_id`, duplicate IDs, IDs that map to the same Agent Registry service (such as `sales_agent` and `sales-agent`), a config with no tables, and low-code modules that fail to import or build.

> [!NOTE]
> Agent Registry entries are stored per location and are **not** replicated between locations. Publish to more than `global` only if a consumer reads from another location, for example `_DEPLOY_ARGS="--registry_locations global us-central1"` on the Cloud Build trigger. Entries that already exist in other locations are left in place; delete them manually if they're no longer needed.

### Agent config schema

Each file in `agent_configs/` describes one Data Agent with the fields the Conversational Analytics API expects ([`_template.json`](agent_configs/_template.json)):

| Field | Required | Maps to |
| :--- | :---: | :--- |
| `agent_id` | Yes | Data Agent ID. Starts with a lowercase letter; lowercase letters, digits, `_` or `-`; max 63 characters. |
| `location` | No | Data Agent location (`global`, `us`, `eu`, etc.). Preserved by `export_agent`; defaults to the CLI `--location` (`global`) when omitted. |
| `display_name`, `description` | No | `DataAgent.display_name`, `DataAgent.description` |
| `labels` | No | `DataAgent.labels` (`{key: value}` map of strings) |
| `source_project_id` | No | Project the agent was exported from. References to it are rewritten to the target project; other projects (e.g. `bigquery-public-data`) are left as-is. |
| `system_instruction` | No | `Context.system_instruction` |
| `table_references` | Yes (≥ 1) | `Context.datasource_references.bq.table_references` (`project_id`, `dataset_id`, `table_id`) |
| `example_queries` | No | `Context.example_queries` (`natural_language_question`, `sql_query`, optional `parameters` with `name`, `description`, `data_type`) |
| `glossary_terms` | No | `Context.glossary_terms` (`display_name`, `description`, `labels`) |
| `options.python_enabled` | No | `Context.options.analysis.python.enabled` (default `false`) |
| `options.model` | No | `Context.options.model` (`"MODEL_UNSPECIFIED"` or `"LATEST_GA_MODEL"`; default `"MODEL_UNSPECIFIED"`) |
| `options.max_billed_bytes` | No | `Context.options.datasource.big_query_max_billed_bytes`: the "Maximum bytes billed" setting in BigQuery Studio. A whole number of bytes; `0` or omitted means no limit. |

Unknown keys are rejected, so typos fail the build instead of being silently ignored.

> [!NOTE]
> The deployer assumes dataset and table names are the same in every environment. The Conversational Analytics API checks that every referenced table exists in the target project, even if it's empty.

Limitations:
- `source_project_id` rewriting handles `project.dataset.table`, `` `project.dataset.table` `` and BigQuery Studio's `` `project`.`dataset`.`table` ``, and only in the project position (a dataset or table that happens to share the project's name is left alone). The legacy `project:dataset.table` form is not rewritten.
- Configs contain only what you enter when building a no-code agent; table and column metadata isn't part of them. BigQuery Studio reads column descriptions directly from the referenced BigQuery tables, so keep descriptions on the tables in every environment (for example in the DDL, dbt or Terraform that creates them) rather than on the agent. Agent-level schema overrides, schema relationships and user functions aren't exported, and because configs are the source of truth, the deployer removes them from target agents.

> [!IMPORTANT]
> Treat Git as the single source of truth in pipeline-managed environments (STG, PRD). A deployment updates both `published_context` (used at runtime) and `staging_context` (rendered by the BigQuery Studio UI) to match the repository, so edits made directly in BigQuery Studio in target environments are detected as drift and reverted on the next run. An empty `display_name`, `description` or `labels` in a definition clears any value set by hand. Make changes in DEV, publish and export them, and promote them through a pull request.

### Adding an agent
- **No-code:** publish the agent in BigQuery Studio, then export it:
  ```bash
  .venv/bin/python -m deployer.export_agent \
      --source projects/<SOURCE_PROJECT>/locations/<LOCATION>/dataAgents/<AGENT_ID> \
      --out agent_configs/<anything>.json [--agent_id my_agent]
  ```
  `--source` also accepts the agent card's `url`. By default the agent deploys under an ID derived from its display name (`"My Sales Agent"` → `my_sales_agent`); use `--agent_id` to choose another. The exporter reads the Data Agent resource (`GetDataAgent`), keeps only the published context, and drops server-managed fields, the staging draft, and settings the config schema doesn't cover (such as agent-level table schemas).
  > **Why not the A2A agent card?** A2A agent cards (from the BigQuery UI or `.../v1/card`) describe how to *call* an agent. They no longer include its tables, instructions or example queries, so they can't be used to recreate it. The deployer still uses the live card to register the agent in Agent Registry.
- You can also write or edit a config by hand, starting from [`_template.json`](agent_configs/_template.json).
- **Low-code:** add `low_code_agents/<anything>.py` defining:
  ```python
  from google.cloud import geminidataanalytics

  AGENT_ID = "my_low_code_agent"

  def build_agent(project_id: str, location: str) -> geminidataanalytics.DataAgent:
      return geminidataanalytics.DataAgent(
          display_name="My low-code agent",
          data_analytics_agent=geminidataanalytics.DataAnalyticsAgent(
              published_context=geminidataanalytics.Context(...)
          )
      )
  ```
  Leave `name` unset; the deployer fills it in from `AGENT_ID`, which follows the same rules as `agent_id`. If you don't set `staging_context`, the deployer copies `published_context` into it, as it does for JSON configs. Files starting with `_` are ignored, so you can use them for shared helpers.

### Gemini Enterprise egress IAM (optional helper)

> [!WARNING]
> `deployer.ge_egress` is a **helper script for connecting agents to a Gemini Enterprise app**. It is **not part of the core deployer**: the pipeline never runs it, and it **isn't covered by the test suite**. Only a few small matching and policy-merge functions in `iam.py` have unit tests. The command itself and the IAM calls it makes (`iam.configure_egress`) are untested. Use it only when you need it, run `--dry-run` first, and review the bindings it reports before applying them.

Run it only after you've added one or more Data Agents to a Gemini Enterprise app. Without it, GE calls to the agent fail with `403: Egress request is not authorized`.

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

It only adds members and never removes any. To run `deployer.ge_egress`, the caller needs `roles/discoveryengine.viewer`, `roles/resourcemanager.projectIamAdmin` and `roles/iap.admin` on the project (plus `roles/serviceusage.serviceUsageConsumer` on the quota project).

### Testing an agent manually
[examples/deploy_a2a.py](examples/deploy_a2a.py) fetches a Data Agent's A2A card and sends it one message over A2A:
```bash
pip install -r examples/requirements.txt
PROJECT_ID=<PROJECT_ID> AGENT_ID=thelook_ecommerce_agent python examples/deploy_a2a.py
```
Set `LOCATION` too if the agent isn't in `global`.

### Running locally
Requires Python 3.11 or later and the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
gcloud auth application-default login
.venv/bin/python -m pytest -q
.venv/bin/python -m deployer --project_id=<PROJECT_ID> --dry-run
```

The test suite is fully offline. It needs no Google Cloud project, credentials or network access, so it runs the same in a fork, locally and in Cloud Build. Google Cloud APIs are replaced by in-memory fakes and `httpx.MockTransport`.
- **Covered:** the core deployer, end to end:
  - config and low-code loading and validation, cross-project rewriting
  - drift detection and update requests
  - the `python -m deployer` command (exit codes, dry-run, error isolation, Agent Registry sync)
  - `python -m deployer.export_agent`
- **Not covered:** the optional `deployer.ge_egress` helper (see above), and behaviour of the live APIs. Use a `--dry-run` against a real project to check the latter.
- **Repo-content tests** validate whatever is in `agent_configs/` and `low_code_agents/`, and skip when those folders are empty, so deleting the samples doesn't break CI.

To change dependencies, edit `requirements.in`, then run `uv pip compile requirements.in -o requirements.txt --python-version 3.11`.

---

## 📋 Prerequisites

### Enable Required Google Cloud APIs
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

---

## 🔐 IAM Permissioning

See [IAM_Mapping.md](IAM_Mapping.md) for the individual permissions each persona needs (Platform Admin, Agent Author, CI/CD service account and End User), why each one is needed, and suggested native and custom roles.

---

## 🛠️ Usage (Deploying via Cloud Build Triggers)

Connect the GitHub repository to **Google Cloud Build** and create triggers under **Cloud Build > Triggers** pointing at `/cloudbuild.yaml`. The pipeline:
1. pulls the previous `agent-deployer:latest` image to use as a layer cache
2. builds the dependencies-only image (`deployer/Dockerfile`)
3. pushes the image (unless `_PUSH_IMAGE=false`), and runs the unit tests at the same time
4. runs `python -m deployer --project_id=$PROJECT_ID ${_DEPLOY_ARGS}`

| Example Trigger Name | Config File | Substitutions | Description |
| :--- | :--- | :--- | :--- |
| **`deploy-bq-data-agent`** *(push to main)* | `/cloudbuild.yaml` | *None* (uses built-in `$PROJECT_ID`) | Creates/updates only the changed Data Agents and syncs Agent Registry (`global` by default). |
| **`plan-bq-data-agent`** *(pull requests)* | `/cloudbuild.yaml` | **`_DEPLOY_ARGS`**: `--dry-run`<br>**`_PUSH_IMAGE`**: `false` | Same pipeline in dry-run mode: reports planned `[CREATE]` / `[MODIFY]` / `[SKIP]` actions without changing anything. |

Other substitutions:
- **`_DEPLOY_ARGS`**: extra deployer flags, e.g. `--registry_locations global us-central1` or `--skip-registry`.
- **`_DEPLOYER_IMAGE`**: the image path. Default `us-docker.pkg.dev/${PROJECT_ID}/agent-repo/agent-deployer`.
- **`_PUSH_IMAGE`**: set to `false` to skip pushing the image (default `true`).

### Securing the pull-request trigger
A pull-request build runs the code **from the pull request**: its `cloudbuild.yaml`, its tests and its `low_code_agents/` modules. Anyone who opens a pull request can change those files, including removing `--dry-run`. So `--dry-run` is not a security boundary. What a PR build can do is limited by how the trigger is configured in Google Cloud, which a pull request can't change:
- **Run it as a separate, read-only service account** (see [IAM_Mapping.md](IAM_Mapping.md#3-cicd-service-account-plan-and-deploy)). Even a malicious build then can't create or modify agents, or push the image.
- **Require a maintainer to comment `/gcbrun`** on the pull request before the build runs (comment control).
- **Protect `main`** with required reviews, since the deploy trigger runs whatever is merged with the deploy service account.

```bash
gcloud builds triggers create github \
    --name=plan-bq-data-agent \
    --repo-owner=<GITHUB_OWNER> --repo-name=<GITHUB_REPO> \
    --pull-request-pattern='^main$' \
    --comment-control=COMMENTS_ENABLED \
    --build-config=cloudbuild.yaml \
    --service-account=projects/<PROJECT_ID>/serviceAccounts/<PLAN_SERVICE_ACCOUNT_EMAIL> \
    --substitutions=_DEPLOY_ARGS=--dry-run,_PUSH_IMAGE=false
```

To run an existing trigger on demand, using what's committed on a branch:
```bash
gcloud builds triggers run deploy-bq-data-agent --branch=main
```

To test the pipeline from your machine without a trigger (uploads your local folder as it is, including uncommitted files):
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
| **`[CREATE]` or `[MODIFY]` fails with `403`/`404` on a table** | A referenced table doesn't exist in the target project, or the Cloud Build service account can't read it. | Create the same datasets and tables in every environment, and give the service account read access as described in [IAM_Mapping.md](IAM_Mapping.md#3-cicd-service-account-plan-and-deploy). |
| **`403: Egress request is not authorized`** when using the agent from Gemini Enterprise | The GE agent's identity is missing the IAP egress access checked by the agent gateway. | Run `python -m deployer.ge_egress --project_id=<PROJECT_ID>` after adding the agent to the GE app. |
| **`409 Resource 'projects/.../locations/global/dataAgents/<id>' already exists`** *(even after deleting the agent)* | Gemini Data Analytics **soft-deletes** `DataAgent` resources and keeps the ID for **30 days**. | Handled automatically: on `AlreadyExists` the deployer calls `update_data_agent()` instead (`deployer/reconcile.py`). |
| **Agent registration returns `200 OK`, but the agent doesn't appear in the Agent Registry UI** | `POST /services` returns a long-running operation that can fail later, e.g. because another service already owns the same interface URL. Missing `name` / `description` / `version` / `url` in `agentSpec.content` also causes failures. | Handled in `deployer/registry.py`: it matches existing services by `url` or `serviceId`, `PATCH`es them instead of creating duplicates, fills required card fields, and polls the operation until it finishes. |
| **`400 Bad Request` from `agentregistry.googleapis.com/.../services?serviceId=...`** | Agent Registry limits `serviceId` and `displayName` to **63 characters**. | Handled in `deployer/registry.py`, which truncates both to 63 characters. |
| **`no compatible transports found`** when an `a2a-sdk` client loads the registered card | Gemini Data Analytics returns `"preferredTransport": "HTTP_JSON"`, but `a2a-sdk` 1.x expects `"HTTP+JSON"`. | Handled in `deployer/registry.py`, which normalizes `preferredTransport` to `HTTP+JSON` before registering. |
| **`403 Forbidden` on `.../dataAgents/<id>/v1/message:send`** | The calling identity lacks permission to chat with the agent or to query its tables. | Grant the caller the End User permissions in [IAM_Mapping.md](IAM_Mapping.md#4-end-user-bigquery-only). Send the `x-goog-user-project` header on A2A requests. |

---

## 🧹 Costs and Clean-up

This pipeline uses billable services: Cloud Build, Artifact Registry storage, and the BigQuery queries the agents run. Check the pricing pages of Conversational Analytics in BigQuery, Agent Registry and Gemini Enterprise for the services you use.

To remove what the pipeline created:
1. Delete the Cloud Build triggers: `gcloud builds triggers delete <TRIGGER_NAME>`.
2. Delete the Data Agents in BigQuery Studio. Deleted agents keep their ID for 30 days.
3. Delete the agents' services from Agent Registry in the Google Cloud console.
4. Delete the image repository: `gcloud artifacts repositories delete agent-repo --location=us`.
5. If you ran `deployer.ge_egress`, remove the IAP egress bindings it added.

---

## License

This project is licensed under the [Apache License 2.0](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) to contribute.

This is not an officially supported Google product. This project is not eligible for the [Google Open Source Software Vulnerability Rewards Program](https://bughunters.google.com/open-source-security).
