#!/usr/bin/env bash
# ==============================================================================
# Shared Utilities, Single Root .env Loader & ADC Auth Bridge
# Compatible with both bash and zsh
# ==============================================================================
set -euo pipefail

_SCRIPT_PATH="${BASH_SOURCE[0]:-${(%):-%x}}"
export DEPLOY_DIR="$(cd "$(dirname "${_SCRIPT_PATH}")" && pwd)"
export REPO_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"

# Load single canonical root .env if present (or fallback to .env.example)
set -a
if [[ -f "${REPO_ROOT}/.env" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
elif [[ -f "${DEPLOY_DIR}/config.env" ]]; then
  # shellcheck disable=SC1091
  source "${DEPLOY_DIR}/config.env"
elif [[ -f "${REPO_ROOT}/.env.example" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env.example"
fi
set +a

export CLOUDSDK_CORE_DISABLE_PROMPTS=1

# Automatic ADC-to-gcloud Auth Bridge & Service Usage API Bootstrap
verify_gcloud_auth() {
  if ! gcloud auth print-access-token >/dev/null 2>&1; then
    if gcloud auth application-default print-access-token >/dev/null 2>&1; then
      echo "ℹ️  Bridging active Application Default Credentials (ADC) token to gcloud CLI..."
      ADC_TOK_FILE="$(mktemp)"
      gcloud auth application-default print-access-token > "${ADC_TOK_FILE}"
      export CLOUDSDK_AUTH_ACCESS_TOKEN_FILE="${ADC_TOK_FILE}"
      export CLOUDSDK_AUTH_ACCESS_TOKEN="$(cat "${ADC_TOK_FILE}")"
    else
      echo "❌ Neither 'gcloud auth login' nor 'gcloud auth application-default login' has an active token."
      echo "👉 Please run: gcloud auth application-default login"
      exit 1
    fi
  fi

  # Ensure serviceusage.googleapis.com & cloudresourcemanager.googleapis.com are enabled on brand-new projects
  # via direct REST call (without X-Goog-User-Project header) before gcloud CLI commands run
  if [[ -n "${PROJECT_ID:-}" && "${PROJECT_ID}" != "your-gcp-project-id" ]]; then
    python3 - <<'PY'
import json, os, ssl, subprocess, time, urllib.request

proj = os.environ.get("PROJECT_ID", "")
tok = os.environ.get("CLOUDSDK_AUTH_ACCESS_TOKEN") or subprocess.check_output(
    ["gcloud", "auth", "application-default", "print-access-token"], text=True
).strip()
ctx = ssl._create_unverified_context()
headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

for svc in ("serviceusage.googleapis.com", "cloudresourcemanager.googleapis.com"):
    url = f"https://serviceusage.googleapis.com/v1/projects/{proj}/services/{svc}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), context=ctx, timeout=15) as r:
            state = json.loads(r.read().decode()).get("state")
        if state == "ENABLED":
            continue
    except Exception:
        pass
    try:
        en_req = urllib.request.Request(f"{url}:enable", data=b"{}", headers=headers, method="POST")
        with urllib.request.urlopen(en_req, context=ctx, timeout=30) as r:
            op = json.loads(r.read().decode())
        op_name = op.get("name", "")
        for _ in range(20):
            if op.get("done") or not op_name or op_name.endswith("DONE_OPERATION"):
                break
            time.sleep(2)
            with urllib.request.urlopen(
                urllib.request.Request(f"https://serviceusage.googleapis.com/v1/{op_name}", headers=headers),
                context=ctx,
                timeout=15,
            ) as r:
                op = json.loads(r.read().decode())
    except Exception:
        pass
PY
    gcloud auth application-default set-quota-project "${PROJECT_ID}" --quiet >/dev/null 2>&1 || true
  fi
}
