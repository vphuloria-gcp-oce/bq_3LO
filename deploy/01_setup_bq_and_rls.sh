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
gcloud config set project "${PROJECT_ID}" --quiet
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

# 4. Create BigQuery Dataset (idempotent)
echo "📁 Ensuring BigQuery dataset '${PROJECT_ID}:${BQ_DATASET}' exists in ${BQ_LOCATION}..."
if ! bq --project_id="${PROJECT_ID}" show --dataset "${PROJECT_ID}:${BQ_DATASET}" >/dev/null 2>&1; then
  bq --location="${BQ_LOCATION}" --project_id="${PROJECT_ID}" mk \
    --dataset \
    --label datacloud:antigravity \
    --description="Retail Store Weekly Sales Dataset for 3-Legged OAuth (3LO) and Row-Level Security (RLS) Demo" \
    "${PROJECT_ID}:${BQ_DATASET}"
else
  echo "   ✅ Dataset ${PROJECT_ID}:${BQ_DATASET} already exists."
fi

# 5. Upload Synthetic Data into BigQuery Table
echo "📤 Uploading 45 synthetic weekly store sales rows to '${PROJECT_ID}:${BQ_DATASET}.${BQ_TABLE}'..."
bq --location="${BQ_LOCATION}" --project_id="${PROJECT_ID}" load \
  --replace \
  --source_format=CSV \
  --skip_leading_rows=1 \
  --label datacloud:antigravity \
  "${PROJECT_ID}:${BQ_DATASET}.${BQ_TABLE}" \
  "${REPO_ROOT}/data/weekly_store_sales.csv" \
  "${REPO_ROOT}/data/weekly_store_sales_schema.json"

# 6. Grant Project IAM Role (roles/bigquery.jobUser) to Regional Users (uno, dos, tres)
echo "🔐 Granting roles/bigquery.jobUser to regional users..."
for REGIONAL_EMAIL in "${NORTH_USER}" "${SOUTH_USER}" "${WEST_USER}"; do
  echo "   • Binding roles/bigquery.jobUser for user:${REGIONAL_EMAIL}..."
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="user:${REGIONAL_EMAIL}" \
    --role="roles/bigquery.jobUser" \
    --condition=None \
    --quiet >/dev/null
done

# 7. Attach Dataset Viewer Access & Row-Level Access Control (RLS) Policies via BigQuery SQL DCL/DDL
# Note: CREATE OR REPLACE ROW ACCESS POLICY automatically grants roles/bigquery.filteredDataViewer
# to each grantee on the table, while GRANT `roles/bigquery.dataViewer` ON SCHEMA grants dataset read access.
echo "🛡️  Granting Dataset Viewer access & attaching Row-Level Access Policies (RLS) on '${PROJECT_ID}.${BQ_DATASET}.${BQ_TABLE}'..."
read -r -d '' RLS_DDL <<EOF || true
/* 0. Dataset-level Data Viewer access for all demo personas */
GRANT \`roles/bigquery.dataViewer\`
ON SCHEMA \`${PROJECT_ID}.${BQ_DATASET}\`
TO "user:${ADMIN_USER}",
   "user:${NORTH_USER}",
   "user:${SOUTH_USER}",
   "user:${WEST_USER}";

/* 1. Admin Policy: Full access to all regions (North, South, West) */
CREATE OR REPLACE ROW ACCESS POLICY rls_admin_all_regions
ON \`${PROJECT_ID}.${BQ_DATASET}.${BQ_TABLE}\`
GRANT TO ("user:${ADMIN_USER}")
FILTER USING (TRUE);

/* 2. North Region Policy: uno@ only sees North stores */
CREATE OR REPLACE ROW ACCESS POLICY rls_north_region
ON \`${PROJECT_ID}.${BQ_DATASET}.${BQ_TABLE}\`
GRANT TO ("user:${NORTH_USER}")
FILTER USING (region = "North");

/* 3. South Region Policy: dos@ only sees South stores */
CREATE OR REPLACE ROW ACCESS POLICY rls_south_region
ON \`${PROJECT_ID}.${BQ_DATASET}.${BQ_TABLE}\`
GRANT TO ("user:${SOUTH_USER}")
FILTER USING (region = "South");

/* 4. West Region Policy: tres@ only sees West stores */
CREATE OR REPLACE ROW ACCESS POLICY rls_west_region
ON \`${PROJECT_ID}.${BQ_DATASET}.${BQ_TABLE}\`
GRANT TO ("user:${WEST_USER}")
FILTER USING (region = "West");
EOF

bq --location="${BQ_LOCATION}" --project_id="${PROJECT_ID}" query \
  --use_legacy_sql=false \
  --label datacloud:antigravity \
  "${RLS_DDL}"

echo "=============================================================================="
echo "✅ BigQuery Table & Row-Level Access Policies Configured!"
echo "👉 Running verification script (scripts/verify_rls_policies.py)..."
echo "=============================================================================="
python3 "${REPO_ROOT}/scripts/verify_rls_policies.py"
