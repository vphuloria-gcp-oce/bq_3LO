#!/usr/bin/env python3
"""
Verification Utility for BigQuery Row-Level Security (RLS) & 3-Legged OAuth (3LO).

Capabilities:
  1. Lists all active Row Access Policies attached to `retail_sales.weekly_store_sales`
     via the BigQuery REST API (`rowAccessPolicies.list`).
  2. Executes a live SQL query using the currently authenticated identity (or an explicit
     gcloud account via `--account <email>`) and reports `SESSION_USER()`, visible rows,
     and regional KPI summaries.
  3. Prints the expected 4-persona RLS isolation reference matrix.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _get_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl._create_unverified_context()


SSL_CTX = _get_ssl_context()


def _gcloud_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")
    return env


def load_config() -> dict[str, str]:
    cfg = {
        "PROJECT_ID": "your-gcp-project-id",
        "BQ_DATASET": "retail_sales",
        "BQ_TABLE": "weekly_store_sales",
        "USER_DOMAIN": "your-company.com",
    }
    cfg_file = REPO_ROOT / ".env"
    if not cfg_file.exists():
        cfg_file = REPO_ROOT / ".env.example"
    if cfg_file.exists():
        for raw_line in cfg_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip().removeprefix("export ").strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip("\"'")
                for ek, ev in cfg.items():
                    v = v.replace(f"${{{ek}}}", ev)
                cfg[k] = v
    for k in list(cfg):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    domain = cfg["USER_DOMAIN"]
    cfg.setdefault("ADMIN_USER", f"admin@{domain}")
    cfg.setdefault("NORTH_USER", f"uno@{domain}")
    cfg.setdefault("SOUTH_USER", f"dos@{domain}")
    cfg.setdefault("WEST_USER", f"tres@{domain}")
    return cfg


def get_access_token(account: str | None = None) -> str:
    env = _gcloud_env()
    cmds = (
        [["gcloud", "auth", "print-access-token", account]]
        if account
        else [
            ["gcloud", "auth", "print-access-token"],
            ["gcloud", "auth", "application-default", "print-access-token"],
        ]
    )
    for cmd in cmds:
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    raise RuntimeError("No valid Google Cloud token found. Run 'gcloud auth login'.")


def list_row_access_policies(project_id: str, dataset_id: str, table_id: str, token: str) -> list[dict]:
    base_url = (
        f"https://bigquery.googleapis.com/bigquery/v2/projects/{project_id}"
        f"/datasets/{dataset_id}/tables/{table_id}/rowAccessPolicies"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with urllib.request.urlopen(urllib.request.Request(base_url, headers=headers), context=SSL_CTX) as resp:
        policies = json.loads(resp.read().decode("utf-8")).get("rowAccessPolicies", [])

    for pol in policies:
        pol_id = pol.get("rowAccessPolicyReference", {}).get("policyId", "")
        if pol_id and not pol.get("grantees"):
            iam_req = urllib.request.Request(f"{base_url}/{pol_id}:getIamPolicy", data=b"{}", headers=headers, method="POST")
            try:
                with urllib.request.urlopen(iam_req, context=SSL_CTX) as iam_resp:
                    bindings = json.loads(iam_resp.read().decode("utf-8")).get("bindings", [])
                    pol["grantees"] = [m for b in bindings for m in b.get("members", [])]
            except Exception:
                pass
    return policies


def run_bq_query(project_id: str, sql: str, token: str) -> list[dict]:
    env = _gcloud_env()
    env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = token
    cmd = [
        "bq", f"--project_id={project_id}", "query",
        "--use_legacy_sql=false", "--format=json", "--label", "app:bq-3lo-agent", sql,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
    return json.loads(res.stdout.strip()) if res.stdout.strip() else []


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify BigQuery Row-Level Security (RLS) policies and query results.")
    parser.add_argument("--account", help="Optional gcloud credentialed email to run the test query as (e.g. uno@...)")
    args = parser.parse_args()

    cfg = load_config()
    fq_table = f"{cfg['PROJECT_ID']}.{cfg['BQ_DATASET']}.{cfg['BQ_TABLE']}"
    token = get_access_token(args.account)

    print("=" * 86)
    print(f"🔍 VERIFYING BIGQUERY ROW-LEVEL SECURITY (RLS) ON: {fq_table}")
    print("=" * 86)

    policies = list_row_access_policies(cfg["PROJECT_ID"], cfg["BQ_DATASET"], cfg["BQ_TABLE"], token)
    print(f"\n1️⃣  Attached Row Access Policies ({len(policies)} policies found):")
    print("-" * 86)
    print(f"{'POLICY ID':<26} | {'FILTER PREDICATE':<24} | {'GRANTEES'}")
    print("-" * 86)
    for pol in policies:
        pol_id = pol.get("rowAccessPolicyReference", {}).get("policyId", "")
        print(f"{pol_id:<26} | {pol.get('filterPredicate', ''):<24} | {', '.join(pol.get('grantees', []))}")
    print("-" * 86)

    summary_sql = f"""
    SELECT
      SESSION_USER() AS current_user,
      region,
      COUNT(*) AS visible_rows,
      ROUND(SUM(net_revenue_usd), 2) AS total_net_revenue_usd,
      ROUND(AVG(target_attainment_pct), 1) AS avg_target_attainment_pct,
      ROUND(AVG(gross_margin_pct), 1) AS avg_gross_margin_pct,
      ROUND(AVG(return_rate_pct), 1) AS avg_return_rate_pct,
      SUM(stockout_incidents) AS total_stockouts
    FROM `{fq_table}`
    GROUP BY region
    ORDER BY region
    """
    rows = run_bq_query(cfg["PROJECT_ID"], summary_sql, token)
    current_user = rows[0]["current_user"] if rows else (args.account or "unknown")
    total_visible = sum(int(r["visible_rows"]) for r in rows)

    print(f"\n2️⃣  Live BigQuery Query Execution as SESSION_USER() = '{current_user}':")
    print(f"    Total Visible Rows: {total_visible} | Visible Regions: {', '.join(r['region'] for r in rows)}")
    print("-" * 86)
    print(f"{'REGION':<8} | {'ROWS':<4} | {'NET REVENUE':<14} | {'TARGET ATT%':<11} | {'MARGIN%':<8} | {'RETURN%':<8} | {'STOCKOUTS'}")
    print("-" * 86)
    for r in rows:
        print(
            f"{r['region']:<8} | {r['visible_rows']:<4} | "
            f"${float(r['total_net_revenue_usd']):>12,.2f} | "
            f"{float(r['avg_target_attainment_pct']):>9.1f}%  | "
            f"{float(r['avg_gross_margin_pct']):>6.1f}%  | "
            f"{float(r['avg_return_rate_pct']):>6.1f}%  | "
            f"{r['total_stockouts']}"
        )
    print("-" * 86)

    print("\n3️⃣  RLS Persona Isolation Matrix:")
    print("-" * 86)
    print(f"{'USER EMAIL':<32} | {'FILTER':<18} | {'VISIBLE DATA':<28} | {'STORYLINE'}")
    print("-" * 86)
    for email, filt, vis, story in [
        (cfg["ADMIN_USER"], "TRUE", "North, South, West (45 rows)", "Full Executive Overview"),
        (cfg["NORTH_USER"], 'region = "North"', "North only (15 rows)", "+18% vs Target (VIP Winter Bundles)"),
        (cfg["SOUTH_USER"], 'region = "South"', "South only (15 rows)", "Margin Bleed & 17.2% Footwear Returns"),
        (cfg["WEST_USER"], 'region = "West"', "West only (15 rows)", "Missed Target due to 170 SKU Stockouts"),
    ]:
        print(f"{email:<32} | {filt:<18} | {vis:<28} | {story}")
    print("=" * 86)


if __name__ == "__main__":
    main()
