# 🤖 Claude Code Instructions (`CLAUDE.md`)

> **Note for Claude Code**: Claude Code automatically loads `CLAUDE.md` at the repository root. Follow the consolidated 4-step deployment protocol below (identical to [`AGENTS.md`](AGENTS.md)) when the user asks you to set up, configure, or deploy this repository to their Google Cloud project. No external skills or plugins outside this repository are required, and all configuration lives in a **single canonical root `.env` file** (copied from [`.env.example`](.env.example)).

## Consolidated 4-Step Deployment Protocol

1. **Ask the User for All Required Inputs in ONE Consolidated Prompt**:
   - First check `gcloud config get-value project` and `gcloud config get-value account` to suggest defaults, then ask in a single message:
     - **`PROJECT_ID`**: Target Google Cloud Project ID.
     - **`ADMIN_USER` & up to 2–3 Regional Test Users (`NORTH_USER`, `SOUTH_USER`, `WEST_USER`)**: `ADMIN_USER` (the person hosting it) gets full access across all regions (`FILTER USING (TRUE)`), while `NORTH_USER`, `SOUTH_USER`, and `WEST_USER` get differential Row-Level Security permissions (`region = 'North'`, `'South'`, `'West'`). Having only 1 or 2 test users is completely fine.
     - **BigQuery Dataset Mode**: Deploy the included synthetic demo dataset (`retail_sales.weekly_store_sales`, 45 rows) or use an existing BigQuery table.
     - **3LO OAuth 2.0 Web Client**: Do they already have `OAUTH_CLIENT_ID` + `OAUTH_CLIENT_SECRET` (or `client_secret_*.json`), or do they need the 3 Cloud Console links to create one?

2. **Run Up-Front Pre-Flight Validator & Surface All Manual Actions Immediately**:
   ```bash
   python3 deploy/00_preflight_and_configure.py \
     --project-id "<PROJECT_ID>" \
     --admin-user "<ADMIN_USER>" \
     --north-user "<NORTH_USER>" \
     --south-user "<SOUTH_USER>" \
     --west-user "<WEST_USER>" \
     --write-config
   ```
   - Writes the single canonical `.env` file at the repository root and checks CLI tools (`gcloud`, `bq`, `uv`), `gcloud` + ADC login, Project access + Cloud Billing status, caller IAM permissions (`testIamPermissions` for API enablement, IAM, BigQuery, and Vertex AI), and 3LO OAuth Web Client credentials.
   - If it exits with code `2`, present **all** manual actions printed by the script at once (`gcloud auth application-default login` or creating the OAuth 2.0 Web Client in Cloud Console with redirect URI `https://vertexaisearch.cloud.google.com/oauth-redirect`) and re-run with `--oauth-client-id` / `--oauth-client-secret` until exit code `0`.

3. **Run the Self-Contained Deployment Scripts**:
   ```bash
   chmod +x deploy/*.sh
   ./deploy/01_setup_bq_and_rls.sh
   ./deploy/02_deploy_agent_and_ge.sh
   ```

4. **Verify & Share Links**:
   Run `python3 scripts/verify_rls_policies.py` and `(cd retail-sales-agent && uv run --no-sync python3 ../scripts/test_agent_personas.py)`, then share the live **Gemini Enterprise Web App URL** printed at the end of `02_deploy_agent_and_ge.sh`.

For full architectural and troubleshooting details, see [`AGENTS.md`](AGENTS.md) and [`README.md`](README.md).
