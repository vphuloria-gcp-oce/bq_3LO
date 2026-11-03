#!/usr/bin/env bash
# ==============================================================================
# Step 1: BigQuery Dataset Upload, IAM Grants & Row-Level Access Policies (RLS)
# ==============================================================================
set -euo pipefail

_SCRIPT_PATH="${BASH_SOURCE[0]:-${(%):-%x}}"
SCRIPT_DIR="$(cd "$(dirname "${_SCRIPT_PATH}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

echo "=============================================================================="
echo "🚀 BigQuery 3LO & Row-Level Security (RLS) Setup"
echo "   • Project ID : ${PROJECT_ID}"
echo "   • Dataset    : ${BQ_DATASET}.${BQ_TABLE} (Location: ${BQ_LOCATION})"
echo "   • Admin User : ${ADMIN_USER} (All Regions: North, South, West)"
echo "   • North User : ${NORTH_USER} (region = 'North')"
echo "   • South User : ${SOUTH_USER} (region = 'South')"
echo "   • West User  : ${WEST_USER} (region = 'West')"
echo "=============================================================================="

# 1. Verify active authentication and set project
verify_gcloud_auth
gcloud config set project "${PROJECT_ID}" --quiet >/dev/null 2>&1 || true
gcloud auth application-default set-quota-project "${PROJECT_ID}" --quiet >/dev/null 2>&1 || true

# 2. Generate synthetic data if not already generated
if [[ ! -f "${REPO_ROOT}/data/weekly_store_sales.csv" ]]; then
  echo "📊 Generating synthetic weekly store sales data..."
  python3 "${REPO_ROOT}/scripts/generate_synthetic_data.py"
fi

# 3. Enable BigQuery & IAM APIs
echo "🔧 Enabling BigQuery and Cloud Resource Manager APIs on ${PROJECT_ID}..."
gcloud services enable \
  bigquery.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# 4. Grant Project IAM Role (roles/bigquery.jobUser) per valid user
echo "🔐 Granting roles/bigquery.jobUser to configured users..."
VALID_USERS=()
for EMAIL in "${ADMIN_USER}" "${NORTH_USER}" "${SOUTH_USER}" "${WEST_USER}"; do
  [[ -z "${EMAIL}" ]] && continue
  if [[ " ${VALID_USERS[*]:-} " == *" ${EMAIL} "* ]]; then
    continue
  fi
  echo "   • Binding roles/bigquery.jobUser for user:${EMAIL}..."
  if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="user:${EMAIL}" \
    --role="roles/bigquery.jobUser" \
    --condition=None \
    --quiet >/dev/null 2>&1; then
    VALID_USERS+=("${EMAIL}")
  else
    echo "   ⚠️  Skipping '${EMAIL}' (account does not exist in Cloud Identity / Google Workspace)."
  fi
done

export VALID_USERS_CSV
VALID_USERS_CSV="$(IFS=,; echo "${VALID_USERS[*]:-}")"

# 5. Provision BigQuery Dataset, Load Synthetic CSV & Attach Row-Level Security (RLS) via Python SDK (ADC)
VENV_PYTHON="${REPO_ROOT}/retail-sales-agent/.venv/bin/python3"
if [[ ! -x "${VENV_PYTHON}" ]]; then
  (cd "${REPO_ROOT}/retail-sales-agent" && uv sync)
fi

"${VENV_PYTHON}" - <<'PY'
import json
import os
from pathlib import Path
import google.auth
from google.cloud import bigquery

repo_root = Path(os.environ["REPO_ROOT"])
project_id = os.environ["PROJECT_ID"]
dataset_id = os.environ["BQ_DATASET"]
table_id = os.environ["BQ_TABLE"]
location = os.environ.get("BQ_LOCATION", "US")

creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
    quota_project_id=project_id,
)
client = bigquery.Client(project=project_id, credentials=creds, location=location)

# 1. Create Dataset (idempotent)
full_dataset_id = f"{project_id}.{dataset_id}"
print(f"📁 Ensuring BigQuery dataset '{full_dataset_id}' exists in {location}...")
ds = bigquery.Dataset(full_dataset_id)
ds.location = location
ds.description = "Retail Store Weekly Sales Dataset for 3-Legged OAuth (3LO) and Row-Level Security (RLS) Demo"
ds.labels = {"app": "bq-3lo-agent"}
client.create_dataset(ds, exists_ok=True)
print(f"   ✅ Dataset {full_dataset_id} ready.")

# 2. Load 45 synthetic store sales rows from CSV
full_table_id = f"{full_dataset_id}.{table_id}"
print(f"📤 Uploading 45 synthetic weekly store sales rows to '{full_table_id}'...")
schema_raw = json.loads((repo_root / "data" / "weekly_store_sales_schema.json").read_text(encoding="utf-8"))
schema = [
    bigquery.SchemaField(
        col["name"],
        col["type"],
        mode=col.get("mode", "NULLABLE"),
        description=col.get("description", ""),
    )
    for col in schema_raw
]
job_config = bigquery.LoadJobConfig(
    schema=schema,
    skip_leading_rows=1,
    source_format=bigquery.SourceFormat.CSV,
    write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    labels={"app": "bq-3lo-agent"},
)
with (repo_root / "data" / "weekly_store_sales.csv").open("rb") as f:
    load_job = client.load_table_from_file(f, full_table_id, job_config=job_config)
load_job.result()
print(f"   ✅ Loaded {client.get_table(full_table_id).num_rows} rows into {full_table_id}.")

# 3. Grant Dataset Viewer & Attach Row Access Policies (RLS)
valid_users = [u.strip() for u in os.environ.get("VALID_USERS_CSV", "").split(",") if u.strip()]
admin_u = os.environ.get("ADMIN_USER", "").strip()
north_u = os.environ.get("NORTH_USER", "").strip()
south_u = os.environ.get("SOUTH_USER", "").strip()
west_u = os.environ.get("WEST_USER", "").strip()

stmts = []
if valid_users:
    grantees = ", ".join(f'"user:{u}"' for u in valid_users)
    stmts.append(f"GRANT `roles/bigquery.dataViewer` ON SCHEMA `{full_dataset_id}` TO {grantees};")

if admin_u in valid_users:
    stmts.append(
        f'CREATE OR REPLACE ROW ACCESS POLICY rls_admin_all_regions ON `{full_table_id}` '
        f'GRANT TO ("user:{admin_u}") FILTER USING (TRUE);'
    )
if north_u in valid_users:
    stmts.append(
        f'CREATE OR REPLACE ROW ACCESS POLICY rls_north_region ON `{full_table_id}` '
        f'GRANT TO ("user:{north_u}") FILTER USING (region = "North");'
    )
if south_u in valid_users:
    stmts.append(
        f'CREATE OR REPLACE ROW ACCESS POLICY rls_south_region ON `{full_table_id}` '
        f'GRANT TO ("user:{south_u}") FILTER USING (region = "South");'
    )
if west_u in valid_users:
    stmts.append(
        f'CREATE OR REPLACE ROW ACCESS POLICY rls_west_region ON `{full_table_id}` '
        f'GRANT TO ("user:{west_u}") FILTER USING (region = "West");'
    )

if stmts:
    print(f"🛡️  Granting Dataset Viewer access & attaching Row-Level Access Policies (RLS) on '{full_table_id}'...")
    ddl_sql = "\n".join(stmts)
    q_job = client.query(ddl_sql, job_config=bigquery.QueryJobConfig(labels={"app": "bq-3lo-agent"}))
    q_job.result()
    print("   ✅ Row Access Policies applied.")
PY

echo "=============================================================================="
echo "✅ BigQuery Table & Row-Level Access Policies Configured!"
echo "👉 Running verification script (scripts/verify_rls_policies.py)..."
echo "=============================================================================="
python3 "${REPO_ROOT}/scripts/verify_rls_policies.py"
