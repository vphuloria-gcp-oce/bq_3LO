# 🤖 AI Coding Assistant Deployment Runbook (`AGENTS.md`)

> **Target Audience**: AI Coding Assistants (**Antigravity**, **Claude Code**, **OpenAI Codex**, **Cursor**, **Windsurf**, **Gemini CLI**, **GitHub Copilot**).
> **Zero External Skills Required**: Everything needed to configure, validate, deploy, and test this repository is self-contained inside `deploy/` and `scripts/`. Do **not** rely on any external plugins or custom skills outside this repository.
> **Single Canonical Config (`.env`)**: Following 12-Factor App best practices, all configuration (GCP Project, BigQuery dataset/table, RLS user emails, and 3LO OAuth Client credentials) lives in a **single root `.env` file** (templated in [`.env.example`](.env.example)). There are no secondary `.env` files or separate `oauth_config.json` files.

When the user asks you to **"Deploy this repo to my GCP project"** (or set up / configure / run this project), follow the **4-Step Fail-Fast Protocol** below in exact order.

---

## 📋 Step 1: Ask the User for All Required Inputs in ONE Consolidated Prompt

Before running any deployment scripts, auto-detect helpful defaults by running `gcloud config get-value project` and `gcloud config get-value account`, and then ask the user for all required inputs in **a single consolidated message**:

1. **`PROJECT_ID`** *(Required)*:
   - What is the target Google Cloud Project ID? (Suggest their active `gcloud` project if set.)
2. **Host Admin & Up to 2–3 Regional Test User Emails** *(Required for BigQuery RLS & Gemini Enterprise Licensing)*:
   - **`ADMIN_USER`**: The person hosting/deploying the project (gets full access to all regions: `FILTER USING (TRUE)`). Defaults to their active `gcloud` email.
   - **`NORTH_USER`**, **`SOUTH_USER`**, **`WEST_USER`**: Up to 2–3 test user emails in their Google Workspace / Cloud Identity domain who will have differential Row-Level Security permissions (`region = 'North'`, `'South'`, `'West'` respectively).
   - *Note*: Tell the user it is completely fine if they only have 1 or 2 test accounts—`deploy/01_setup_bq_and_rls.sh` automatically skips any placeholder emails that do not exist in Cloud Identity without failing.
3. **BigQuery Dataset Mode** *(Default: Option A)*:
   - **Option A (Recommended for demo/sandbox)**: Provision the included synthetic `retail_sales.weekly_store_sales` table (45 rows across `North`, `South`, and `West` regions) with pre-configured Row-Level Security (RLS) policies.
   - **Option B (Existing Table)**: Point `BQ_DATASET` and `BQ_TABLE` in `.env` to an existing BigQuery table.
4. **3-Legged OAuth (3LO) Web Client Credentials**:
   - Do they already have an OAuth 2.0 Web Client (`OAUTH_CLIENT_ID` + `OAUTH_CLIENT_SECRET` or a downloaded `client_secret_*.json` file), or should you print the 60-second Cloud Console links to create one?

---

## 🔍 Step 2: Run the Up-Front Pre-Flight Validator & Surface All Manual Actions Immediately

As soon as the user replies to Step 1, run [`deploy/00_preflight_and_configure.py`](deploy/00_preflight_and_configure.py) to write the single root `.env` file and run **all** environment, credential, billing, IAM permission, and OAuth checks up-front:

```bash
python3 deploy/00_preflight_and_configure.py \
  --project-id "<PROJECT_ID>" \
  --admin-user "<ADMIN_USER>" \
  --north-user "<NORTH_USER>" \
  --south-user "<SOUTH_USER>" \
  --west-user "<WEST_USER>" \
  --write-config
```

*(If the user already provided `--oauth-client-id` and `--oauth-client-secret`, pass those flags too. If they chose an existing table, pass `--bq-dataset` and `--bq-table`. If they want Vertex AI Agent Runtime only without Gemini Enterprise, pass `--skip-gemini-enterprise`.)*

### What `00_preflight_and_configure.py` Verifies Up-Front
1. **Single Canonical Config**: Writes `.env` at the repository root.
2. **CLI Tools & Python Env**: Verifies `gcloud`, `bq`, `uv`, and syncs `retail-sales-agent/.venv`.
3. **Authentication**: Verifies active `gcloud` token and Application Default Credentials (ADC).
4. **Project & Billing**: Verifies that `PROJECT_ID` exists, is `ACTIVE`, resolves its numeric `projectNumber`, and checks that Cloud Billing is enabled.
5. **Caller IAM Permissions (`testIamPermissions`)**: Verifies up-front that the caller holds `serviceusage.services.enable`, `resourcemanager.projects.setIamPolicy`, `bigquery.datasets.create`, and `aiplatform.reasoningEngines.create`.
6. **3LO OAuth Web Client & Redirect URIs**: Verifies that `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` are set in `.env` (or auto-discovered from `client_secret_*.json`) and checks for the Gemini Enterprise redirect URI (`https://vertexaisearch.cloud.google.com/oauth-redirect`).

### If Exit Code is `2` (Manual User Action Needed)
Pause and present **all** flagged manual actions to the user at once so they can complete them in one go:

- **If `gcloud` or ADC login is needed**:
  ```bash
  gcloud auth login <ADMIN_USER>
  gcloud auth application-default login
  gcloud auth application-default set-quota-project <PROJECT_ID>
  ```
- **If the 3LO OAuth 2.0 Web Client (`OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET`) is needed**:
  Google Cloud requires OAuth 2.0 Web Clients to be created in the Cloud Console. Give the user these direct links for their project:
  1. **Branding & Audience** *(if first time in project — set User Type to **Internal**)*:
     - `https://console.cloud.google.com/auth/branding?project=<PROJECT_ID>`
     - `https://console.cloud.google.com/auth/audience?project=<PROJECT_ID>`
  2. **Create Web Client**:
     - Open `https://console.cloud.google.com/auth/clients/create?project=<PROJECT_ID>`
     - Choose **Web application** and add these **Authorized redirect URIs**:
       - `https://vertexaisearch.cloud.google.com/oauth-redirect` *(Required for Gemini Enterprise)*
       - `http://localhost:8000/dev-ui/` *(Optional, for local `adk web` testing)*
  3. **Provide Credentials**:
     - Paste `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` into the root `.env` file (or tell you so you can run `python3 deploy/00_preflight_and_configure.py --oauth-client-id "<ID>" --oauth-client-secret "<SECRET>" --write-config`).

Re-run `python3 deploy/00_preflight_and_configure.py` until it exits with code `0` (`🎉 ALL PRE-FLIGHT CHECKS PASSED!`).

---

## 🚀 Step 3: Execute the Self-Contained Deployment Scripts

Once `deploy/00_preflight_and_configure.py` exits with code `0`, run both deployment scripts automatically:

### 3A. Provision BigQuery Dataset, Synthetic Data & Row-Level Security (RLS) Policies
*(Run unless the user opted to use an existing table that already has RLS policies)*:
```bash
chmod +x deploy/*.sh
./deploy/01_setup_bq_and_rls.sh
```
- Creates `retail_sales.weekly_store_sales` in `<PROJECT_ID>`, loads 45 rows of synthetic store sales across `North`, `South`, and `West`, binds `roles/bigquery.jobUser` + dataset `roles/bigquery.dataViewer`, attaches the Row Access Policies (`CREATE OR REPLACE ROW ACCESS POLICY`), and runs `scripts/verify_rls_policies.py`.

### 3B. Deploy ADK Agent with SPIFFE `AGENT_IDENTITY` & Publish to Gemini Enterprise
```bash
./deploy/02_deploy_agent_and_ge.sh
```
- **Zero External CLI Dependency**: Works whether `agents-cli` is installed or not (automatically falls back to `deploy/deploy_agent_runtime.py` using the `vertexai` SDK inside `retail-sales-agent/.venv` and `deploy/manage_ge_app.py` using Discovery Engine REST APIs).
- Injects `BQ_PROJECT_ID`, `BQ_DATASET`, `BQ_TABLE`, `BQ_LOCATION`, `GEMINI_AUTH_ID`, `OAUTH_CLIENT_ID`, and `OAUTH_CLIENT_SECRET` from the root `.env` file directly as container environment variables (`--set-env-vars` / `env_vars`).
- Provisions the Gemini Enterprise App, assigns user licenses, creates the `bq-3lo-auth` Discovery Engine `Authorization` resource, deploys the ADK agent with `identity_type = AGENT_IDENTITY`, binds least-privilege Plane 1 IAM roles (`roles/aiplatform.user`, `roles/logging.logWriter`, `roles/telemetry.writer`) to the agent's SPIFFE principal (with **zero** BigQuery roles), registers the agent in Gemini Enterprise with `ALL_USERS` sharing, and prints the live **Gemini Enterprise Web App URL**.

---

## 🧪 Step 4: Verify & Present Results to the User

After `./deploy/02_deploy_agent_and_ge.sh` completes:

1. **Run the automated verification scripts**:
   ```bash
   # Verify BigQuery RLS policies and row filtering:
   python3 scripts/verify_rls_policies.py

   # Test the ADK agent locally with the user's OAuth token:
   (cd retail-sales-agent && uv run --no-sync python3 ../scripts/test_agent_personas.py)
   ```
2. **Present the final summary to the user**:
   - **Gemini Enterprise Web App URL**: Printed at the end of `./deploy/02_deploy_agent_and_ge.sh` (tell the user they can open it in an incognito window as `ADMIN_USER` or one of their regional test users, click **Authorize** on first prompt, and see BigQuery RLS filter rows by `SESSION_USER()`).
   - **Vertex AI Agent Engine Console (`Identity` Tab)**: `https://console.cloud.google.com/vertex-ai/agents/agent-engines?project=<PROJECT_ID>`
   - **Custom Frontend / CLI Integration Test**: `(cd retail-sales-agent && uv run --no-sync python3 ../scripts/custom_frontend_3lo_client_example.py --project-id <PROJECT_ID>)`
