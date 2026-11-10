# Web application and Azure architecture

The web app replaces the command line for day-to-day use. A preparer uploads the drop folder and picks the client and quarter, a worker runs the same pipeline the CLI runs, and a reviewer inspects, corrects and approves the draft in the browser before the final PDF exists.

The pipeline (`src/pmr/`) did not change in substance. The web layer (`src/pmr_web/`) only moves files, records who did what, and shows what the pipeline wrote. Every figure still comes from code, every sentence is still source-checked, and approval still goes through `review.approve` and `pipeline.finalize`.

## What a reviewer sees

| Screen | What it shows | What the reviewer can do |
|---|---|---|
| Reports | Every client and quarter, its status (Processing, Blocked, In review, Approved, Final), blockers and warnings | Open a report, start a new one |
| New report | Client (code or full name), quarter, folder, file or zip upload, or the sample drop folder | Create the report; the first draft is queued at once. If no flash in the files is for that client and quarter, nothing is created and the page lists the clients and quarters the files can report on |
| Overview | Live progress while a run is active (stage, percent, log of each step and model answer). Then: blocker and warning counts, numbers checked against the manifest, workbook citations re-read, footing checks, missing figures, files used and skipped, every issue, featured funds, compliance, commitments | Open the cell, page or slide behind any issue or commitment |
| Figures | All 535 printed figures and quoted passages, each with its formula and locators | Filter; open any locator beside the table |
| Narrative | Every printed sentence with its quotes; sentences dropped by the source checks with the reason | Open the evidence (the PDF page with the quote highlighted, or the slide); edit, exclude, restore, revert |
| Chart values | Each deck chart image beside every value the model read from it, with plausibility checks and which values the market update cites | Correct a misread value |
| Draft PDF | The draft, marked DRAFT FOR REVIEW | Read or download it |
| Approval | The approval gate (draft finished, no blockers, every reviewer change drafted, no run in progress), three review statements, the approval record, the final PDF | Approve, withdraw approval, generate the final PDF |
| Inputs | Every uploaded file with size, SHA-256 and the discovery decision (used or skipped, and why) | Upload more files, replace or remove one |
| Audit trail | Every run with its duration and content hash; every upload, reviewer change, approval and finalization with actor and time, stored in UTC and shown in the viewer's time zone (US Eastern if unknown) | Read only |

### The evidence viewer

The viewer reads the uploaded source file, not the pipeline's output, so a reviewer checks the printed figure against the source rather than against the pipeline's own reading.

- **Workbook cell** (`flash_4Q25.xlsx#CashActivity(Agg)!F11`): the cell highlighted in a grid of the rows and columns around it, plus the row label and the column header.
- **PDF page** (`prior_pmr_3Q25.pdf#p10`, a manager report): the page rendered with each quoted passage highlighted.
- **Slide** (`market_outlook_4Q25.pptx#slide4/image1`): the slide's text with quoted lines highlighted, and the chart image.

### Editing narrative without losing traceability

A reviewer can reword a sentence, but the rewording keeps the drafted sentence's evidence, so it may not add facts. `narrative.edit_problems` refuses an edit that introduces a number or a capitalized name not present in the drafted sentence or its quotes. The web app runs the check when the reviewer saves, and the pipeline runs it again when it applies `edited_sentences` from `review.json`. An edited sentence keeps `edited_from` in `facts.json`, so the reviewer page and the audit trail show both wordings.

Exclusions, edits and chart corrections all live in `review.json`, the same file the CLI uses. The content hash covers them, so any change after approval voids the approval.

### Approval

Approval is refused unless:

1. a draft finished and is the one in storage;
2. no blocker is open;
3. every saved reviewer change is in that draft (the app tracks a revision counter per report);
4. no run is queued or running;
5. the reviewer ticks all three review statements.

The approval records the signed-in user (Entra ID in Azure), the UTC time and the SHA-256 content hash. "Generate the final PDF" queues a finalize run: the worker rebuilds the draft from the stored inputs and calls `pipeline.finalize`, which writes the final PDF only if the rebuilt content still matches the approved hash, then verifies the final PDF and deletes it if verification fails.

## Architecture

```
                Reviewer's browser
                       |
                       |  HTTPS, Entra ID sign-in (App Service authentication)
                       |  (full design; the slim deployment is described under Deploying to Azure)
                       v
   +------------------------------------------+        +----------------------------+
   | Azure App Service: web app (Flask)       |        | Azure Container Apps:      |
   |  upload, review screens, approval        |        | worker                     |
   |  queues runs, never runs the pipeline    |        |  claims queued runs        |
   +----+-------------+-------------+---------+        |  runs src/pmr unchanged    |
        |             |             |                  +---+---------+---------+----+
        |             |             |                      |         |         |
        v             v             v                      v         v         v
   PostgreSQL     Blob Storage   Key Vault  <------------- same three resources
   (reports,     (inputs,       (DB URL, session key,
    runs, audit,  outputs,       sign-in secret)
    the queue)    review.json,                             |
                  model cache)                             v
                                                    Azure AI Foundry
                                                    (Claude Opus 5.5)

   Both apps send traces, logs and request telemetry to Application Insights.
   Both apps use one user-assigned managed identity; no storage key or model key exists.
```

### Why each service is there

| Need | Service | Why this one |
|---|---|---|
| Host the reviewer UI | **Azure App Service** (Linux container) | Built-in Entra ID sign-in (App Service authentication) without auth code in the app; Key Vault references resolve secrets into settings; health checks and deployment slots. |
| Run the pipeline in the background | **Azure Container Apps** (worker) | A run takes 8 seconds cached and about 2 minutes with live model calls. That should not hold a web request open. Container Apps runs the same image with a different command and no time limit per run. Azure Functions was the other option; its consumption plan caps execution time and needs a different packaging model, and the pipeline gains nothing from being event-triggered. |
| Store documents | **Azure Blob Storage** | Uploaded inputs, every pipeline output, `review.json` and the shared model-answer cache. Versioning and soft delete keep any overwritten or deleted file recoverable, which the audit trail relies on. Shared-key access is disabled, so only the managed identity can read it. |
| Application state | **Azure Database for PostgreSQL** | Reports, runs, uploaded-file hashes and the append-only audit trail. Also the work queue (see below). Nothing the report says lives here; that stays in `facts.json` and `manifest.json` in Blob Storage. |
| Secrets | **Azure Key Vault** | The database URL, the session-signing key and the Entra app secret. App Service and Container Apps read them through Key Vault references with the managed identity, so the code only ever reads environment variables and never calls Key Vault itself. |
| Model access | **Azure AI Foundry** (Claude) | Keeps model traffic inside the Azure tenant, under the firm's Azure agreement, with Entra ID auth instead of an API key. `llm.py` switches to `AnthropicFoundry` when `ANTHROPIC_FOUNDRY_RESOURCE` is set. The cache key does not name the provider, so the committed answers replay identically. |
| Monitoring | **Application Insights** | The Azure Monitor OpenTelemetry distro instruments Flask requests, SQL queries and outgoing calls to Blob Storage and the model. The worker logs "run finished" and "run failed" with run and report ids. |
| Sign-in | **Microsoft Entra ID** | Through App Service authentication. The app records the signed-in user's name on every action and on the approval, so an approval cannot be made under another name. |

### Services left out, and why

- **Service Bus.** The PostgreSQL `runs` table is the queue. A worker claims a run with a conditional `UPDATE ... WHERE status = 'queued'`, which succeeds for exactly one worker, so several replicas are safe. A firm produces a few dozen reports a quarter; a broker would add a service without solving a problem. If volume grows, Service Bus plus a KEDA scaler on the Container App is the change.
- **Azure AI Search.** Discovery classifies files by content in code and every claim must be checked verbatim against its source. Semantic retrieval would add a non-deterministic step to a pipeline whose value is that it has none outside the model's prose.
- **Azure AI Document Intelligence.** The inputs are digital PDFs, workbooks and a deck. PyMuPDF, openpyxl and python-pptx read them exactly, with cell and page locators. Document Intelligence would earn its place for scanned manager reports.

## How the original guarantees carry over

| Guarantee | Where it lives in the web version |
|---|---|
| Deterministic figures | The worker calls `pipeline.run` unchanged. The browser never computes or edits a figure. |
| Source-level traceability | Every figure and sentence in the UI opens the exact cell, page or slide it cites, read from the uploaded file. |
| Validation and reconciliation | Footing, manifest and locator checks run as before; the Overview shows their counts and every failure. |
| Human approval | Approval goes through `review.approve` with the signed-in identity; finalization through `pipeline.finalize`, which rebuilds and compares the content hash. |
| Reproducibility | Every run records the SHA-256 of each input it read and the content hash it produced. The model cache is shared in Blob Storage, so a rebuild replays the same answers. |
| Auditability | Append-only audit events in PostgreSQL; blob versioning for files. |
| Incomplete inputs | A missing flash or prior PMR still produces facts with issues; the UI shows them. A run that crashes is marked failed with its error, and the worker moves on. |

## Running locally

```bash
python -m pip install -r requirements-web.txt
python -m pmr_web                      # http://127.0.0.1:5001
```

Locally the app uses SQLite and a folder (`webdata/`, ignored by git), runs the worker as a thread inside the web process, and signs you in by name. Set `PMR_NO_LLM=1` to replay cached model answers only. "Start from the sample drop folder" on the New report screen loads `inputs/`, and with the committed cache the CPERS 4Q25 draft builds in about 8 seconds.

| Setting | Local default | Azure |
|---|---|---|
| `PMR_STORAGE` | `local` | `azure` |
| `PMR_DATA_DIR` | `./webdata` | `/tmp/pmr` (evidence viewer cache only) |
| `DATABASE_URL` | `sqlite:///webdata/pmr_web.db` | Key Vault reference to `postgresql+psycopg://...` |
| `PMR_EMBEDDED_WORKER` | `1` | `1` in the slim deployment; `0` in the full design (the worker is its own Container App) |
| `PMR_ACCESS_CODE` | unset (name only) | Slim: Key Vault reference to the shared demo code. Full: unset |
| `PMR_TRUST_EASY_AUTH` | unset (off) | Slim: `0`. Full: `1`, so the app reads the Entra ID user from App Service authentication headers |
| `ANTHROPIC_API_KEY` | `.env` or environment | Key Vault reference, when an API key is given instead of Foundry |
| `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_STORAGE_CONTAINER` | unused | set by OpenTofu |
| `ANTHROPIC_FOUNDRY_RESOURCE` | unset (Anthropic API, or cache only) | Foundry resource name |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | unset (no telemetry) | set by OpenTofu |

## Deploying to Azure

Two OpenTofu configurations live in `infra/`. CI runs `tofu validate` and `tofu test` on both, offline, against mocked providers.

| | `infra/tofu/` (slim, deployed) | `infra/tofu-full/` (full design, not deployed) |
|---|---|---|
| Web app | App Service B1 | App Service P0v3 |
| Worker | Thread inside the web app | Container App, always on |
| Sign-in | Name plus a shared access code | Entra ID through App Service authentication |
| Model | Anthropic API key in Key Vault | Azure AI Foundry, or an API key in Key Vault |
| Shared | PostgreSQL B1ms, Blob Storage, Key Vault, Container Registry Basic, Application Insights | Same |
| Approx. cost | $36/month | $120 to $150/month |

The slim deployment serves a demo audience: a handful of reports, visitors without accounts in the tenant. The worker thread claims runs from the same PostgreSQL queue, so moving to the full design changes no code, only settings.

### The access code

Without App Service authentication, App Service does not strip client-sent `X-MS-*` headers, so the slim app never trusts them (`PMR_TRUST_EASY_AUTH=0`). Visitors sign in with a name and the shared code instead. The code is generated by OpenTofu, stored in Key Vault, and read with:

```bash
tofu output -raw access_code
```

To change it, run `tofu apply -replace=random_password.access_code` and restart the web app.

### Deploy the slim configuration

1. Create a storage account for state and grant yourself **Storage Blob Data Contributor** on it. Copy `backend.hcl.example` to `backend.hcl` and `terraform.tfvars.example` to `terraform.tfvars` and fill them in. Both files are ignored by git.
2. Apply:
   ```bash
   cd infra/tofu
   az login
   export TF_VAR_state_passphrase=...     # 16+ characters; encrypts state and plan files
   export TF_VAR_anthropic_api_key=...    # optional; without it the app runs cache-only
   tofu init -backend-config=backend.hcl
   tofu plan -out=pmr.tfplan
   tofu apply pmr.tfplan
   ```
   The account that runs `apply` needs **Owner** or **User Access Administrator**, because the configuration creates role assignments.
3. Set the deploy workflow's repository variables from the outputs:
   ```bash
   gh variable set AZURE_CLIENT_ID       --body "$(tofu output -raw deploy_client_id)"
   gh variable set AZURE_TENANT_ID       --body "$(tofu output -raw tenant_id)"
   gh variable set AZURE_SUBSCRIPTION_ID --body "$(tofu output -raw subscription_id)"
   gh variable set AZURE_REGISTRY        --body "$(tofu output -raw registry)"
   gh variable set AZURE_RESOURCE_GROUP  --body "$(tofu output -raw resource_group)"
   gh variable set AZURE_WEB_APP         --body "$(tofu output -raw web_app)"
   ```
4. Push to `main`. `.github/workflows/deploy.yml` runs the web tests, builds the image, pushes it tagged with the commit hash, points the web app at it and waits until `/version` reports that commit. `/health` alone is not enough, because the old container keeps answering until the new one is up. It signs in through OpenID Connect as the `id-<prefix>-deploy` identity, whose federated credential accepts only this repository's `main` branch. That identity holds **AcrPush** on the registry and **Website Contributor** on the web app, nothing else.

### Region and quota limits on new subscriptions

New subscriptions often have zero App Service quota and restricted PostgreSQL SKUs in some regions. On this subscription in November 2026:

| Region | App Service B1 | PostgreSQL B1ms |
|---|---|---|
| eastus2 | quota 0 | `SkuNotAvailable` |
| westus3 | available | `SkuNotAvailable` |
| westus2 | available | available |
| centralus, southcentralus, canadacentral | quota 0 | not tested |

The deployment runs in westus2. `az postgres flexible-server list-skus` listed B1ms as available in eastus2 and westus3 even though creation failed there, so only a real create proves a region works.

### Deploy the full design

`infra/tofu-full/` needs an Entra ID app registration for sign-in:

1. Create the app registration with a client secret, and the state storage account as above.
2. Fill in `backend.hcl` and `terraform.tfvars` (`auth_client_id` is required), then:
   ```bash
   cd infra/tofu-full
   export TF_VAR_state_passphrase=...
   export TF_VAR_auth_client_secret=...
   export TF_VAR_anthropic_api_key=...    # or set foundry_resource_id instead
   tofu init -backend-config=backend.hcl
   tofu plan -out=pmr.tfplan && tofu apply pmr.tfplan
   ```
3. Add the `auth_redirect_uri` output to the app registration's redirect URIs.
4. Build and push the image, then restart the web app:
   ```bash
   az acr build -r $(tofu output -raw registry) -t pmr-web:latest ../..
   az webapp restart -g $(tofu output -raw resource_group) -n $(tofu output -raw web_app)
   ```
5. For model calls inside the tenant, deploy Claude in an Azure AI Foundry resource and set `foundry_resource_id`. This grants the managed identity **Cognitive Services User** on it. With neither Foundry nor an API key the apps run cache-only, and new inputs show marked placeholders, as the CLI does without a key.

Both configurations keep the generated database password and session key in state. OpenTofu encrypts state and plan files with the passphrase and refuses to write them unencrypted. Losing the passphrase means losing the state.

### Production hardening not in either configuration

- Private endpoints for PostgreSQL, Blob Storage and Key Vault, with the apps on a virtual network.
- Entra ID authentication to PostgreSQL instead of a password.
- An Entra app role (for example `PMR.Approver`) checked before approval, so preparers and approvers are separate people.
- A deployment slot, so a new image is health-checked before it takes traffic.
- Rate limiting on the slim sign-in page.
