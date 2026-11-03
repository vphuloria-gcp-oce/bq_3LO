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

# 2. Provision Gemini Enterprise App, Licenses & 3LO OAuth Authorization
echo "🏢 Provisioning Gemini Enterprise App, User Licenses & 3LO OAuth Config..."
GE_ENV_FILE="$(mktemp)"
trap 'rm -f "${GE_ENV_FILE}"' EXIT

python3 "${DEPLOY_DIR}/manage_ge_app.py" --prepare \
  --project-id "${PROJECT_ID}" --engine-id "${GE_APP_ID}" --display-name "${GE_APP_DISPLAY_NAME}" \
  --user-emails "${ADMIN_USER},${NORTH_USER:-},${SOUTH_USER:-},${WEST_USER:-}" --auth-id "${GEMINI_AUTH_ID}" \
  --bq-dataset "${BQ_DATASET}" --bq-table "${BQ_TABLE}" --bq-location "${BQ_LOCATION}" \
  --oauth-client-id "${OAUTH_CLIENT_ID:-}" --oauth-client-secret "${OAUTH_CLIENT_SECRET:-}" \
  --output-env "${GE_ENV_FILE}"

# shellcheck disable=SC1090
source "${GE_ENV_FILE}"

# 3. Verify local ADK import, deploy to Agent Runtime & publish to Gemini Enterprise
echo "📦 Syncing & verifying ADK agent dependencies..."
(
  cd "${AGENT_DIR}"
  uv sync --quiet || uv sync
  uv run --no-sync python3 -c "import app.fast_api_app; print('   ✅ Local import of app.fast_api_app succeeded!')"

  AGENT_SERVICE_NAME="${AGENT_SERVICE_NAME:-retail-sales-agent-id}"
  RUNTIME_ENV_VARS="BQ_PROJECT_ID=${PROJECT_ID},BQ_DATASET=${BQ_DATASET},BQ_TABLE=${BQ_TABLE},BQ_LOCATION=${BQ_LOCATION},GEMINI_AUTH_ID=${GEMINI_AUTH_ID}"
  if [[ -n "${OAUTH_CLIENT_ID:-}" && -n "${OAUTH_CLIENT_SECRET:-}" ]]; then
    RUNTIME_ENV_VARS="${RUNTIME_ENV_VARS},OAUTH_CLIENT_ID=${OAUTH_CLIENT_ID},OAUTH_CLIENT_SECRET=${OAUTH_CLIENT_SECRET}"
  fi

  echo "🚀 Deploying ADK Agent with SPIFFE Agent Identity (AGENT_IDENTITY) to Vertex AI Agent Runtime (${REGION})..."
  uv run --no-sync python3 "${DEPLOY_DIR}/deploy_agent_runtime.py" \
    --project-id "${PROJECT_ID}" \
    --region "${REGION}" \
    --service-name "${AGENT_SERVICE_NAME}"

  AGENT_RUNTIME_ID="$(python3 -c "import json; d=json.load(open('deployment_metadata.json')); print(d.get('remote_agent_runtime_id') or d.get('remote_agent_engine_id'))")"
  echo "   ✅ Deployed Agent Runtime ID (AGENT_IDENTITY): ${AGENT_RUNTIME_ID}"

  echo "🔐 Verifying & binding Plane 1 SPIFFE Agent Identity IAM roles..."
  python3 "${DEPLOY_DIR}/manage_ge_app.py" \
    --project-id "${PROJECT_ID}" \
    --bind-agent-identity "${AGENT_RUNTIME_ID}"

  if [[ "${SKIP_GEMINI_ENTERPRISE:-false}" != "true" ]] && command -v agents-cli >/dev/null 2>&1; then
    PUBLISH_ARGS=(
      publish gemini-enterprise --registration-type adk
      --agent-runtime-id "${AGENT_RUNTIME_ID}" --gemini-enterprise-app-id "${GE_ENGINE_RESOURCE}"
      --display-name "${AGENT_DISPLAY_NAME}" --description "${AGENT_DESCRIPTION}"
      --tool-description "${AGENT_DESCRIPTION}" --project-id "${PROJECT_ID}" --project-number "${PROJECT_NUMBER}"
    )
    if [[ -n "${GE_AUTH_RESOURCE:-}" ]]; then
      PUBLISH_ARGS+=(--authorization-id "${GE_AUTH_RESOURCE}")
    fi
    echo "🌐 Publishing ADK Agent to Gemini Enterprise..."
    agents-cli "${PUBLISH_ARGS[@]}" || echo "ℹ️  Updating Gemini Enterprise registration via Discovery Engine REST API..."
  fi
)

# 4. Create/Patch Agent Sharing (ALL_USERS) + 3LO Auth, deduplicate GE registrations & print live URL
ACTIVE_RUNTIME_ID="$(python3 -c "import json; d=json.load(open('${AGENT_DIR}/deployment_metadata.json')); print(d.get('remote_agent_runtime_id') or d.get('remote_agent_engine_id'))")"
GE_WEB_URL=""
if [[ "${SKIP_GEMINI_ENTERPRISE:-false}" != "true" ]]; then
  echo "🔄 Finalizing Gemini Enterprise Agent Registration, Sharing (ALL_USERS) & 3LO Auth..."
  GE_WEB_URL="$(python3 "${DEPLOY_DIR}/manage_ge_app.py" \
    --project-id "${PROJECT_ID}" --engine-id "${GE_APP_ID}" \
    --auth-id "${GEMINI_AUTH_ID}" \
    --agent-runtime-id "${ACTIVE_RUNTIME_ID}" \
    --finalize-agent-display-name "${AGENT_DISPLAY_NAME}")"
fi

echo "=============================================================================="
echo "✅ ADK Agent (SPIFFE Agent Identity) Deployed & Published to Gemini Enterprise!"
if [[ -n "${GE_WEB_URL}" ]]; then
  echo "   🌐 Gemini Enterprise Web App : ${GE_WEB_URL}"
fi
echo "   • Active Reasoning Engine   : ${ACTIVE_RUNTIME_ID}"
echo "   • Gemini Enterprise Console : https://console.cloud.google.com/gen-app-builder/engines?project=${PROJECT_ID}"
echo "   • Vertex AI Agent Runtime   : https://console.cloud.google.com/vertex-ai/agents/agent-engines?project=${PROJECT_ID}"
echo "=============================================================================="
