#!/usr/bin/env bash
# ==============================================================================
# Step 2: Deploy ADK 3LO Agent to Vertex AI Agent Runtime & Publish to Gemini Enterprise
# ==============================================================================
set -euo pipefail

_SCRIPT_PATH="${BASH_SOURCE[0]:-${(%):-%x}}"
SCRIPT_DIR="$(cd "$(dirname "${_SCRIPT_PATH}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

AGENT_DIR="${REPO_ROOT}/retail-sales-agent"
GE_APP_ID="${GE_APP_ID:-omni-retail-sales-ge}"
GE_APP_DISPLAY_NAME="${GE_APP_DISPLAY_NAME:-OmniRetail 3LO Sales Assistant}"
AGENT_DISPLAY_NAME="${AGENT_DISPLAY_NAME:-OmniRetail BigQuery 3LO Sales Agent}"
AGENT_DESCRIPTION="${AGENT_DESCRIPTION:-Analyzes weekly store sales, margins, returns, and stockouts across regions using per-user 3-Legged OAuth (3LO) and BigQuery Row-Level Security (RLS).}"
GEMINI_AUTH_ID="${GEMINI_AUTH_ID:-bq-3lo-auth}"

echo "=============================================================================="
echo "🤖 Deploying ADK 3LO Agent & Publishing to Gemini Enterprise"
echo "   • Project ID            : ${PROJECT_ID} (${REGION})"
echo "   • Gemini Enterprise App : ${GE_APP_DISPLAY_NAME} (${GE_APP_ID})"
echo "   • Agent Display Name    : ${AGENT_DISPLAY_NAME}"
echo "   • Licensed Personas     : ${ADMIN_USER}, ${NORTH_USER}, ${SOUTH_USER}, ${WEST_USER}"
echo "=============================================================================="

# 1. Verify authentication & enable APIs
verify_gcloud_auth
gcloud config set project "${PROJECT_ID}" --quiet

echo "🔧 Enabling Vertex AI, Discovery Engine, Cloud Build & Artifact Registry APIs..."
gcloud services enable \
  aiplatform.googleapis.com discoveryengine.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# 2. Provision Gemini Enterprise App, Licenses, 3LO Auth & write app/oauth_config.json
echo "🏢 Provisioning Gemini Enterprise App, User Licenses & 3LO OAuth Config..."
GE_ENV_FILE="$(mktemp)"
trap 'rm -f "${GE_ENV_FILE}"' EXIT

python3 "${DEPLOY_DIR}/manage_ge_app.py" --prepare \
  --project-id "${PROJECT_ID}" --engine-id "${GE_APP_ID}" --display-name "${GE_APP_DISPLAY_NAME}" \
  --user-emails "${ADMIN_USER},${NORTH_USER},${SOUTH_USER},${WEST_USER}" --auth-id "${GEMINI_AUTH_ID}" \
  --bq-dataset "${BQ_DATASET}" --bq-table "${BQ_TABLE}" --bq-location "${BQ_LOCATION}" \
  --oauth-client-id "${OAUTH_CLIENT_ID:-}" --oauth-client-secret "${OAUTH_CLIENT_SECRET:-}" \
  --agent-oauth-config "${AGENT_DIR}/app/oauth_config.json" --output-env "${GE_ENV_FILE}"

# shellcheck disable=SC1090
source "${GE_ENV_FILE}"

# 3. Verify local ADK import, deploy to Agent Runtime & publish to Gemini Enterprise
echo "📦 Verifying ADK agent dependencies, deploying & publishing..."
(
  cd "${AGENT_DIR}"
  uv run --no-sync python3 -c "import app.fast_api_app; print('   ✅ Local import of app.fast_api_app succeeded!')"
  uv lock --no-config --default-index https://pypi.org/simple

  echo "🚀 Deploying ADK Agent to Vertex AI Agent Runtime (${REGION})..."
  agents-cli deploy -d agent_runtime --project "${PROJECT_ID}" --region "${REGION}" --no-confirm-project

  AGENT_RUNTIME_ID="$(python3 -c "import json; d=json.load(open('deployment_metadata.json')); print(d.get('remote_agent_runtime_id') or d.get('remote_agent_engine_id'))")"
  echo "   ✅ Deployed Agent Runtime ID: ${AGENT_RUNTIME_ID}"

  PUBLISH_ARGS=(
    publish gemini-enterprise --registration-type adk
    --agent-runtime-id "${AGENT_RUNTIME_ID}" --gemini-enterprise-app-id "${GE_ENGINE_RESOURCE}"
    --display-name "${AGENT_DISPLAY_NAME}" --description "${AGENT_DESCRIPTION}"
    --tool-description "${AGENT_DESCRIPTION}" --project-id "${PROJECT_ID}" --project-number "${PROJECT_NUMBER}"
  )
  if [[ -n "${GE_AUTH_RESOURCE}" ]]; then
    PUBLISH_ARGS+=(--authorization-id "${GE_AUTH_RESOURCE}")
  fi

  echo "🌐 Publishing ADK Agent to Gemini Enterprise..."
  agents-cli "${PUBLISH_ARGS[@]}"
)

# 4. Patch Agent Sharing (ALL_USERS) + 3LO Auth & print live Gemini Enterprise Web App URL
echo "🔄 Finalizing Agent Sharing (ALL_USERS) & fetching Gemini Enterprise Web App URL..."
GE_WEB_URL="$(python3 "${DEPLOY_DIR}/manage_ge_app.py" \
  --project-id "${PROJECT_ID}" --engine-id "${GE_APP_ID}" \
  --auth-id "${GEMINI_AUTH_ID}" --finalize-agent-display-name "${AGENT_DISPLAY_NAME}")"

echo "=============================================================================="
echo "✅ ADK Agent Deployed & Published to Gemini Enterprise!"
if [[ -n "${GE_WEB_URL}" ]]; then
  echo "   🌐 Gemini Enterprise Web App : ${GE_WEB_URL}"
fi
echo "   • Gemini Enterprise Console : https://console.cloud.google.com/gen-app-builder/engines?project=${PROJECT_ID}"
echo "   • Vertex AI Agent Runtime   : https://console.cloud.google.com/vertex-ai/agents/agent-engines?project=${PROJECT_ID}"
echo "=============================================================================="
