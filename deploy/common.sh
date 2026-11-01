#!/usr/bin/env bash
# ==============================================================================
# Shared Utilities, Config Loader & Corp mTLS/ADC Auth Bridge
# Compatible with both bash and zsh
# ==============================================================================
set -euo pipefail

_SCRIPT_PATH="${BASH_SOURCE[0]:-${(%):-%x}}"
export DEPLOY_DIR="$(cd "$(dirname "${_SCRIPT_PATH}")" && pwd)"
export REPO_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"

# Load user config if present, otherwise fallback to config.env.example defaults
if [[ -f "${DEPLOY_DIR}/config.env" ]]; then
  # shellcheck disable=SC1091
  source "${DEPLOY_DIR}/config.env"
else
  # shellcheck disable=SC1091
  source "${DEPLOY_DIR}/config.env.example"
fi

# Disable Corporate Context-Aware mTLS / ECP Proxy hangs on macOS workstations
export CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE=false
export CLOUDSDK_CONTEXT_AWARE_USE_ECP_HTTP_PROXY=false
export CLOUDSDK_CONTEXT_AWARE_USE_MTLS_FOR_GRPC=false
export GOOGLE_API_USE_CLIENT_CERTIFICATE=false
export GOOGLE_API_USE_MTLS_ENDPOINT=never
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
export CLOUDSDK_BILLING_QUOTA_PROJECT="${PROJECT_ID}"
export CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"

# Automatic ADC-to-gcloud Auth Bridge
verify_gcloud_auth() {
  if ! gcloud auth print-access-token >/dev/null 2>&1; then
    if gcloud auth application-default print-access-token >/dev/null 2>&1; then
      echo "ℹ️  Bridging active Application Default Credentials (ADC) token to gcloud CLI..."
      export CLOUDSDK_AUTH_ACCESS_TOKEN="$(gcloud auth application-default print-access-token)"
    else
      echo "❌ Neither 'gcloud auth login' nor 'gcloud auth application-default login' has an active token."
      echo "👉 Please run: gcloud auth login ${ADMIN_USER} && gcloud auth application-default login"
      exit 1
    fi
  fi
}
