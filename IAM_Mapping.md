# IAM Mapping

> [!NOTE]
> This document was prepared using a combination of public documentation and API calls from testing and validation of the accompanying source code.

This document lists the **individual IAM permissions**, not roles, needed to build a data agent in DEV, promote it with this repo's pipeline, and use it in BigQuery.

## Scope

**In scope:** four personas. (1) The **Platform Admin**, who does the one-time setup. (2) The **Agent Author**, who builds the agent in BigQuery Studio in DEV and exports it with `python -m deployer.export_agent`. (3) The **CI/CD service account**, which runs the pull-request plan (dry run) and the deploy after merge. (4) The **End User**, who chats with the promoted agent in BigQuery.

**Out of scope:** Gemini Enterprise (admins, agent identities and the `deployer.ge_egress` helper), A2A and Agent Registry consumers, Data Studio, Knowledge Catalog data stewards (creating glossaries and terms), and agent observability.

---

## Summary matrix

✔ = needed. "plan" = pull-request dry run; "deploy" = apply after merge.

| Permission | 1. Admin | 2. Author | 3. CI/CD (plan) | 3. CI/CD (deploy) | 4. End User |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `serviceusage.services.enable` | ✔ | | | | |
| `resourcemanager.projects.getIamPolicy` / `setIamPolicy` | ✔ | | | | |
| `iam.serviceAccounts.create` | ✔ | | | | |
| `iam.serviceAccounts.actAs` | ✔ | | | | |
| `artifactregistry.repositories.create` | ✔ | | | | |
| `cloudbuild.builds.create` | ✔ | | | | |
| `geminidataanalytics.dataAgents.create` | | ✔ | | ✔ | |
| `geminidataanalytics.dataAgents.get` | | ✔ | ✔ | ✔ | ✔ |
| `geminidataanalytics.dataAgents.list` | | ✔ | | | ✔ |
| `geminidataanalytics.dataAgents.update` | | ✔ | | ✔ | |
| `geminidataanalytics.dataAgents.chat` | | ✔ | | | ✔ |
| `geminidataanalytics.dataAgents.getIamPolicy` / `setIamPolicy` | | ✔ | | | |
| `geminidataanalytics.dataAgents.delete` | | ✔ | | | |
| `geminidataanalytics.operations.get` | | ✔ | | ✔ | |
| `cloudaicompanion.topics.create` (+ `cloudaicompanion.user` set) | | ✔ | | | ✔ |
| `bigquery.jobs.create` | | ✔ | | | ✔ |
| `bigquery.datasets.get`, `bigquery.tables.get` | | ✔ | | ✔ | ✔ |
| `bigquery.tables.list` | | ✔ | | | |
| `bigquery.tables.getData` | | ✔ | | | ✔ |
| `datacatalog.catalogs.searchAll`, `dataplex.projects.search`, `dataplex.entries.get/list` | | ✔ | | | |
| `dataplex.glossaries.get/list`, `dataplex.glossaryTerms.get/list`, `dataplex.glossaryCategories.get/list`, `dataplex.entryLinks.get` | | only if importing* | | | only if not imported* |
| `agentregistry.services.list` | | | ✔ | ✔ | |
| `agentregistry.services.create` / `update`, `agentregistry.operations.get` | | | | ✔ | |
| `artifactregistry.repositories.downloadArtifacts` | | | ✔ | ✔ | |
| `artifactregistry.repositories.uploadArtifacts`, `artifactregistry.tags.create/update` | | | | ✔ | |
| `logging.logEntries.create` | | | ✔ | ✔ | |
| `serviceusage.services.use` | | ✔ | ✔ | ✔ | |
| `datacatalog.categories.fineGrainedGet` / `bigquery.dataPolicies.maskedGet` | | if needed | | | if needed |

\* Business glossary access depends on whether the Agent Author imports the terms into the agent. **Authors** need it only to import terms ([§2d](#2d-import-knowledge-catalog-business-glossary-terms-optional)). **End users** need `dataplex.glossaryTerms.get`, `dataplex.glossaries.get` and `dataplex.entryLinks.get` only when the terms were **not** imported and stay attached to the tables in Knowledge Catalog ([§4d](#4d-knowledge-catalog-business-glossary-terms)).

---

## 1. Platform Admin

Done once per environment (DEV, STG, PRD).

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `serviceusage.services.enable` | Each project | Enable BigQuery, Gemini Data Analytics, Gemini for Google Cloud, Knowledge Catalog, Agent Registry, Cloud Build and Artifact Registry. |
| `resourcemanager.projects.getIamPolicy` / `resourcemanager.projects.setIamPolicy` | Each project | Grant the permissions in §2–4. For narrower grants, use the resource-level equivalents: `artifactregistry.repositories.setIamPolicy`, `bigquery.datasets.update`, `geminidataanalytics.dataAgents.setIamPolicy`. |
| `iam.serviceAccounts.create` | STG/PRD projects | Create the CI/CD service account. Separate accounts for plan and deploy are recommended. |
| `artifactregistry.repositories.create` | Deployment project (`us`) | Create the `agent-repo` Docker repository used by `cloudbuild.yaml`. |
| `cloudbuild.builds.create` | Deployment project | Create the plan (pull-request) and deploy (merge) Cloud Build triggers, and run builds manually if needed. |
| `iam.serviceAccounts.actAs` | CI/CD service account(s) | Attach the service account to the triggers. |

> [!NOTE]
> Platform Admin permissions are powerful, `setIamPolicy` especially. Keep the group small, and don't give these permissions to the CI/CD service account.

---

## 2. Agent Author (includes export)

Works in **DEV**: builds, tests and publishes the agent in BigQuery Studio, then exports it to `agent_configs/` with `python -m deployer.export_agent`.

### 2a. Build, publish and share the agent

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `geminidataanalytics.dataAgents.create` | DEV project | Create the agent. The creator is automatically granted `dataAgentOwner` on it. |
| `geminidataanalytics.operations.get` | DEV project | Wait for create and update operations to finish. |
| `geminidataanalytics.dataAgents.get` / `.list` | DEV project | Open the agent and see it in the agent list. |
| `geminidataanalytics.dataAgents.update` | Agent | Edit settings and **publish** (which copies the draft to the published version). Also covers custom glossary terms, verified queries and the "Maximum bytes billed" setting, all stored on the agent. |
| `geminidataanalytics.dataAgents.chat` | Agent | Test the agent in the preview chat. |
| `geminidataanalytics.dataAgents.getIamPolicy` / `.setIamPolicy` | Agent | Share the DEV agent with reviewers. |
| `geminidataanalytics.dataAgents.delete` | Agent | Delete experiments. Deleted agents keep their ID for 30 days. |

### 2b. Choose knowledge sources

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `datacatalog.catalogs.searchAll`, `dataplex.projects.search` | DEV project | Search for tables to add. |
| `dataplex.entries.get` / `.list` | Projects with the tables | Read catalog entries and generated insights for chosen tables. |
| `bigquery.datasets.get`, `bigquery.tables.get`, `bigquery.tables.list` | Datasets used | Browse and read schemas, including the column descriptions the agent picks up from BigQuery. |

### 2c. Test with real data

The agent queries with the author's own identity.

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `bigquery.tables.getData` | Referenced tables and views | Read data to answer test questions. |
| `bigquery.jobs.create` | DEV billing project | Run the generated SQL. |
| `cloudaicompanion.topics.create` + the rest of `roles/cloudaicompanion.user` | DEV project | The Gemini-powered chat. |
| `datacatalog.categories.fineGrainedGet` / `bigquery.dataPolicies.maskedGet` | Only for tables with policy tags or masking | Same rules as end users (§4c). |

### 2d. Import Knowledge Catalog business glossary terms (optional)

The author's Knowledge Catalog access depends on one choice: whether to **import** business glossary terms into the agent definition.

| Author's choice | Does the author need Knowledge Catalog access? |
| :--- | :--- |
| **Import the terms into the agent** while building it in DEV | **Yes:** the permissions below, to find and read the terms. Saving them on the agent uses `geminidataanalytics.dataAgents.update` (§2a). |
| **Don't import.** Leave the terms attached to the BigQuery tables in Knowledge Catalog, for end users to benefit from directly. | **No.** Instead, end users need glossary read access (§4d). |

Permissions, **only when importing terms into the agent:**

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `dataplex.glossaries.get` / `.list` | Glossary project and location | Find the glossaries that contain the terms to import. |
| `dataplex.glossaryCategories.get` / `.list` | Glossary | Browse categories. |
| `dataplex.glossaryTerms.get` / `.list` | Glossary | Read the term definitions being imported. Knowledge Catalog **hides** terms you can't `get`, so they can't be imported. |
| `dataplex.entryLinks.get` | Entry groups holding term-to-table links | See which terms are already linked to the agent's tables and columns. |

Imported terms are stored in the agent's own glossary (`Context.glossary_terms`, its only glossary field), so `export_agent` writes them to the config's `glossary_terms` and they're promoted with the rest of the agent. If a term changes in Knowledge Catalog, re-import it into the DEV agent and re-export.

Creating and editing terms in Knowledge Catalog is a data-steward task, which is out of scope here.


### 2e. Export the agent

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `geminidataanalytics.dataAgents.get` | The DEV agent being exported | `export_agent` reads the published definition with `GetDataAgent`. Already covered by 2a. |
| `serviceusage.services.use` | The author's ADC quota project | Client libraries bill API usage to the quota project set by `gcloud auth application-default login`. |

No permissions are needed to run `pytest`; the suite is fully offline.

---

## 3. CI/CD service account (plan and deploy)

This is the account Cloud Build runs as.
- **Plan:** pull-request builds run `python -m deployer --dry-run` and don't push the image.
- **Deploy:** merge builds apply changes.

Using **two separate accounts** is recommended, so pull-request code can't change anything. If you use one account, it needs the "deploy" set.

### 3a. Data agents

| Permission | Plan | Deploy | Scope | Why |
| :--- | :---: | :---: | :--- | :--- |
| `geminidataanalytics.dataAgents.get` | ✔ | ✔ | Target project | Read the live agent to decide CREATE / MODIFY / SKIP. |
| `geminidataanalytics.dataAgents.create` | | ✔ | Target project | Create new agents. `roles/geminidataanalytics.dataAgentEditor` doesn't include this permission. The account becomes owner of the agents it creates. |
| `geminidataanalytics.dataAgents.update` | | ✔ | Each agent | Write the config to the agent, revert hand-made changes, and restore soft-deleted agents. |
| `geminidataanalytics.operations.get` | | ✔ | Target project | Wait for create and update to finish. |
| `bigquery.tables.get` | | ✔ | Every referenced table | The API rejects an agent whose referenced tables don't exist or can't be read. Metadata access is probably enough. |

### 3b. Agent Registry

No-code agents are published here; skip this section with `--skip-registry`.

| Permission | Plan | Deploy | Scope | Why |
| :--- | :---: | :---: | :--- | :--- |
| `agentregistry.services.list` | ✔ | ✔ | Each registry location (default `global`) | Find existing entries, and report missing ones in the plan. |
| `agentregistry.services.create` | | ✔ | Each location | Register new agents. |
| `agentregistry.services.update` | | ✔ | Each location | Update an existing entry instead of creating a duplicate. |
| `agentregistry.operations.get` | | ✔ | Each location | Wait for the registry operation to finish. |
| `geminidataanalytics.dataAgents.get` | | ✔ | Each agent | Fetch the agent's A2A card to publish it (`…/v1/card`). |
| `serviceusage.services.use` | ✔ | ✔ | Target project | Required by the quota-project header (`x-goog-user-project`) the deployer sends. |

### 3c. Build mechanics

| Permission | Plan | Deploy | Scope | Why |
| :--- | :---: | :---: | :--- | :--- |
| `artifactregistry.repositories.downloadArtifacts` | ✔ | ✔ | `agent-repo` | Pull the cached deployer image and run the test and deploy steps in it. |
| `artifactregistry.repositories.uploadArtifacts` | | ✔ | `agent-repo` | Push the image (`push-image` step). |
| `artifactregistry.tags.create` / `.update` | | ✔ | `agent-repo` | Set the `:latest` and `:<BUILD_ID>` tags. |
| `logging.logEntries.create` | ✔ | ✔ | Target project | Write build logs. |

**Deliberately excluded:** `dataAgents.delete`, `dataAgents.setIamPolicy`, `dataAgents.list`, `dataAgents.chat`, and any IAM-admin permission. The pipeline never deletes agents, shares them, or changes IAM.

---

## 4. End User (BigQuery only)

The user chats with a promoted agent in BigQuery Studio, or with the Conversational Analytics API from their own identity. **The agent uses the user's own permissions**: without access to the source tables, chats fail with `Schema_Resolution: Access Denied`.

### 4a. Agent access

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `geminidataanalytics.dataAgents.chat` | **The agent** (preferred) or project | Chat with the agent. Granting it on the agent shares only that agent. |
| `geminidataanalytics.dataAgents.get` | Agent | Load the agent to start a conversation. |
| `geminidataanalytics.dataAgents.list` | Project | See agents in the BigQuery Studio list. Not needed when opening a shared link. |
| `cloudaicompanion.topics.create` + the rest of `roles/cloudaicompanion.user` | **Project** | Required at the project level to create conversations. |

### 4b. Data access

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `bigquery.jobs.create` | The user's billing project | Run the SQL the agent generates. |
| `bigquery.tables.getData` | Every referenced table or view | Read query results. |
| `bigquery.tables.get`, `bigquery.datasets.get` | Every referenced table and dataset | Resolve schemas, including column descriptions. |

### 4c. Conditional access

| Permission or requirement | When |
| :--- | :--- |
| `datacatalog.categories.fineGrainedGet` | Tables with **column-level security** (policy tags) |
| `bigquery.dataPolicies.maskedGet` | Tables with **data masking** |
| Membership in the table's **row-access policy** (not a permission) | Tables with **row-level security** |

### 4d. Knowledge Catalog business glossary terms

Whether end users need Knowledge Catalog access depends on what the Agent Author did ([§2d](#2d-import-knowledge-catalog-business-glossary-terms-optional)):

| Did the author import the terms into the agent? | Where the terms live | Do end users need Knowledge Catalog access? | Promotion to STG/PRD |
| :--- | :--- | :--- | :--- |
| **Yes** | In the agent's configuration (`glossary_terms`) | **No.** The terms are part of the agent, so users chatting with it need no Knowledge Catalog permissions. | Automatic: the terms are in the exported config. |
| **No** (terms only attached to the BigQuery tables or columns in Knowledge Catalog) | In Knowledge Catalog, as term-to-table links | **Yes:** the permissions below. Without them, the terms are hidden from the user and the agent answers without that context. | Not carried by the config. The terms must also be linked to the **STG/PRD** tables in Knowledge Catalog. |

Permissions, **only when the author did not import the terms:**

| Permission | Scope | Why |
| :--- | :--- | :--- |
| `dataplex.glossaryTerms.get` | Glossary terms linked to the agent's tables | Read the definitions of terms attached to the tables. Terms the user can't `get` are hidden. |
| `dataplex.glossaries.get` | Glossary containing those terms | Resolve the glossary the terms belong to. |
| `dataplex.entryLinks.get` | Entry groups holding the term-to-table links | See which terms are attached to the referenced tables and columns. |

All three are in `roles/dataplex.catalogViewer`, which can be granted on the glossary.

> [!TIP]
> Import the terms into the agent when you need the promoted agent to behave the same everywhere without giving end users Knowledge Catalog access.

> [!TIP]
> Don't grant end users `dataAgents.update`, `setIamPolicy` or `delete` in STG/PRD. The CI/CD pipeline owns those agents, and any edit made by hand is reverted on the next deploy.

---

## Appendix

### Suggested native roles

> [!NOTE]
> These are Google Cloud **predefined roles** that together cover every permission listed for each persona in this document. Most of them also grant **more permissions than strictly required**. For example, `roles/bigquery.dataViewer` also allows exporting tables, and `roles/agentregistry.editor` can also delete registry entries. They're a simpler starting point than custom roles. For least privilege, use the [suggested custom roles](#suggested-custom-roles) instead.

#### 1. Platform Admin

| Role | Grant on | Covers |
| :--- | :--- | :--- |
| `roles/serviceusage.serviceUsageAdmin` | Each project | `serviceusage.services.enable` |
| `roles/resourcemanager.projectIamAdmin` | Each project | `resourcemanager.projects.getIamPolicy` / `setIamPolicy` |
| `roles/iam.serviceAccountAdmin` | STG/PRD projects | `iam.serviceAccounts.create` |
| `roles/iam.serviceAccountUser` | The CI/CD service account(s) | `iam.serviceAccounts.actAs` |
| `roles/artifactregistry.admin` | Deployment project | `artifactregistry.repositories.create` |
| `roles/cloudbuild.builds.editor` | Deployment project | `cloudbuild.builds.create` (triggers and manual builds) |

`roles/owner` also covers all of these, but grants far more. Avoid it for day-to-day administration.

#### 2. Agent Author (DEV)

| Role | Grant on | Covers |
| :--- | :--- | :--- |
| `roles/geminidataanalytics.dataAgentCreator` | DEV project | `dataAgents.create`, `operations.get`. The author automatically becomes `dataAgentOwner` of each agent they create, which covers `get`, `update`, `chat`, `getIamPolicy`, `setIamPolicy` and `delete` on **their own** agents. |
| `roles/geminidataanalytics.dataAgentViewer` | DEV project | `dataAgents.get` / `list`: see every agent in the project. The creator role doesn't include `list`. Use `roles/geminidataanalytics.dataAgentOwner` on the project instead if authors must edit and share each other's agents. |
| `roles/cloudaicompanion.user` | DEV project | `cloudaicompanion.topics.create` and the rest of the Gemini chat permissions |
| `roles/bigquery.jobUser` | DEV billing project | `bigquery.jobs.create` |
| `roles/bigquery.dataViewer` | Datasets used as knowledge sources | `bigquery.datasets.get`, `bigquery.tables.get` / `list` / `getData` |
| `roles/datacatalog.viewer` | DEV project | `datacatalog.catalogs.searchAll`, `dataplex.projects.search`, `dataplex.entries.get` / `list`. It also includes the glossary read permissions needed to **import** Knowledge Catalog terms (`dataplex.glossaries.*`, `glossaryTerms.*`, `glossaryCategories.*` get/list, `entryLinks.get`) for glossaries in the same project. |
| `roles/dataplex.catalogViewer` | Glossary project | **Only if importing terms from a glossary in another project.** Same glossary read permissions as above. |
| `roles/serviceusage.serviceUsageConsumer` | The author's ADC quota project | `serviceusage.services.use`, for `export_agent` |
| `roles/datacatalog.categoryFineGrainedReader` / `roles/bigquerydatapolicy.maskedReader` | Policy tags / data policies | **Only for tables with column-level security or masking:** `datacatalog.categories.fineGrainedGet` / `bigquery.dataPolicies.maskedGet` |

#### 3. CI/CD service account

| Role | Plan | Deploy | Grant on | Covers |
| :--- | :---: | :---: | :--- | :--- |
| `roles/geminidataanalytics.dataAgentViewer` | ✔ | | Target project | `dataAgents.get` (read-only plan) |
| `roles/geminidataanalytics.dataAgentCreator` | | ✔ | Target project | `dataAgents.create`, `operations.get`. The account automatically owns the agents it creates. `dataAgentEditor` alone doesn't include `create`. |
| `roles/geminidataanalytics.dataAgentEditor` | | ✔ | Target project | `dataAgents.get` / `update`, `operations.get`, for agents the account **didn't** create, e.g. ones created before the pipeline existed |
| `roles/bigquery.metadataViewer` | | ✔ | Each referenced dataset | `bigquery.tables.get`, for the API's referenced-table check. Use `roles/bigquery.dataViewer` if metadata access turns out not to be enough. |
| `roles/agentregistry.viewer` | ✔ | | Target project | `agentregistry.services.list` |
| `roles/agentregistry.editor` | | ✔ | Target project | `agentregistry.services.list` / `create` / `update`, `agentregistry.operations.get` |
| `roles/artifactregistry.reader` | ✔ | | `agent-repo` | `artifactregistry.repositories.downloadArtifacts` |
| `roles/artifactregistry.writer` | | ✔ | `agent-repo` | `downloadArtifacts`, `uploadArtifacts`, `artifactregistry.tags.create` / `update` |
| `roles/logging.logWriter` | ✔ | ✔ | Target project | `logging.logEntries.create` |
| `roles/serviceusage.serviceUsageConsumer` | ✔ | ✔ | Target project | `serviceusage.services.use` |

With a single account, grant the **Deploy** column, since its roles also cover everything the plan needs.

#### 4. End User (BigQuery)

| Role | Grant on | Covers |
| :--- | :--- | :--- |
| `roles/geminidataanalytics.dataAgentUser` | **The agent** (preferred) or the project | `dataAgents.chat` / `get` / `list`. Grant it on the project only if users should see and chat with every agent there. |
| `roles/cloudaicompanion.user` | Project (required at project level) | `cloudaicompanion.topics.create` and the rest of the Gemini chat permissions |
| `roles/bigquery.jobUser` | The user's billing project | `bigquery.jobs.create` |
| `roles/bigquery.dataViewer` | Every referenced dataset (or table/view) | `bigquery.tables.get` / `getData`, `bigquery.datasets.get` |
| `roles/datacatalog.categoryFineGrainedReader` | Policy tags | **Only for column-level security:** `datacatalog.categories.fineGrainedGet` |
| `roles/bigquerydatapolicy.maskedReader` | Data policies | **Only for data masking:** `bigquery.dataPolicies.maskedGet` |
| `roles/dataplex.catalogViewer` | Glossary | **Only if the author did not import the glossary terms into the agent** ([§4d](#4d-knowledge-catalog-business-glossary-terms)): `dataplex.glossaryTerms.get`, `dataplex.glossaries.get`, `dataplex.entryLinks.get` |

Row-level security isn't a role: add users to the table's row-access policy.

### Suggested custom roles

These come straight from the tables above. Before using them in production, confirm them in a sandbox, particularly `bigquery.tables.get` for the table-existence check and `dataAgents.get` for the card fetch, which aren't documented per permission.

```yaml
# 2. Agent Author, on the DEV project
title: data-agent-author
includedPermissions:
  - geminidataanalytics.dataAgents.create
  - geminidataanalytics.dataAgents.get
  - geminidataanalytics.dataAgents.list
  - geminidataanalytics.dataAgents.update
  - geminidataanalytics.dataAgents.chat
  - geminidataanalytics.dataAgents.getIamPolicy
  - geminidataanalytics.dataAgents.setIamPolicy
  - geminidataanalytics.dataAgents.delete
  - geminidataanalytics.operations.get
  - datacatalog.catalogs.searchAll
  - dataplex.projects.search
  - dataplex.entries.get
  - dataplex.entries.list
  - bigquery.jobs.create
  - serviceusage.services.use
# Plus: roles/cloudaicompanion.user on DEV; BigQuery data read on the DEV datasets;
# Only if importing Knowledge Catalog terms into agents: dataplex.glossaries/glossaryTerms/glossaryCategories get+list
# and dataplex.entryLinks.get, where the glossaries live.
```

```yaml
# 3. CI/CD service account, deploy mode, on each target project
title: data-agent-deployer
includedPermissions:
  - geminidataanalytics.dataAgents.get
  - geminidataanalytics.dataAgents.create
  - geminidataanalytics.dataAgents.update
  - geminidataanalytics.operations.get
  - agentregistry.services.list
  - agentregistry.services.create
  - agentregistry.services.update
  - agentregistry.operations.get
  - logging.logEntries.create
  - serviceusage.services.use
# Plus, on agent-repo: artifactregistry.repositories.{downloadArtifacts,uploadArtifacts}, artifactregistry.tags.{create,update}
# Plus, on each referenced dataset: bigquery.tables.get (start with roles/bigquery.metadataViewer)
```

```yaml
# 3. CI/CD service account, plan mode (pull requests), on each target project
title: data-agent-planner
includedPermissions:
  - geminidataanalytics.dataAgents.get
  - agentregistry.services.list
  - logging.logEntries.create
  - serviceusage.services.use
# Plus, on agent-repo: artifactregistry.repositories.downloadArtifacts
```

```yaml
# 4. End User, on each target project (grant the agent permissions on the agent itself where possible)
title: data-agent-end-user
includedPermissions:
  - geminidataanalytics.dataAgents.chat
  - geminidataanalytics.dataAgents.get
  - geminidataanalytics.dataAgents.list
  - bigquery.jobs.create
# Plus: roles/cloudaicompanion.user on the project; bigquery.tables.{get,getData} and bigquery.datasets.get on referenced data;
# plus §4c as applicable. Only if the author did NOT import the glossary terms into the agent (§4d):
# dataplex.glossaryTerms.get, dataplex.glossaries.get, dataplex.entryLinks.get (e.g. roles/dataplex.catalogViewer on the glossary).
```
