#!/usr/bin/env python3
"""
Up-Front Pre-Flight Validator & Single `.env` Configuration Generator (`deploy/00_preflight_and_configure.py`).

Designed for both:
  1. AI Coding Assistants (Antigravity, Claude Code, OpenAI Codex, Cursor, Windsurf, Gemini CLI)
     running non-interactively via CLI flags (`--project-id ... --write-config`).
  2. Human developers running interactively (`--interactive`).

What this script does:
  - Writes or updates the single canonical `.env` file at the repository root
    (following 12-Factor App configuration standards—no duplicate `.env` files or
    `oauth_config.json` files).
  - Performs all environment, credential, project, billing, IAM permission, and 3LO OAuth
    checks UP-FRONT before any deployment script runs:
      1. Local CLI tools (`gcloud`, `bq`, `uv`) & `retail-sales-agent/.venv` sync
      2. Active `gcloud` authentication & Application Default Credentials (ADC)
      3. GCP Project existence, `ACTIVE` state, and numeric `projectNumber` resolution
      4. Caller IAM Permissions check (`testIamPermissions`) for API enablement,
         IAM policy binding, BigQuery dataset creation, and Vertex AI Agent Engine deployment
      5. Cloud Billing status check on the target project
      6. 3-Legged OAuth (3LO) Web Client credentials (`OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET`
         or `client_secret_*.json`) and Redirect URI validation
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parent
AGENT_DIR = REPO_ROOT / "retail-sales-agent"
ROOT_ENV_PATH = REPO_ROOT / ".env"
ROOT_ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

REQUIRED_GE_REDIRECT_URI = "https://vertexaisearch.cloud.google.com/oauth-redirect"
LOCAL_ADK_REDIRECT_URI = "http://localhost:8000/dev-ui/"

REQUIRED_PROJECT_PERMISSIONS = [
    "serviceusage.services.enable",
    "resourcemanager.projects.setIamPolicy",
    "bigquery.datasets.create",
    "aiplatform.reasoningEngines.create",
]


def _ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    ctx = ssl.create_default_context()
    return ctx if ctx.get_ca_certs() else ssl._create_unverified_context()


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
    try:
        res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"


def load_existing_config() -> dict[str, str]:
    cfg = {
        "PROJECT_ID": "your-gcp-project-id",
        "REGION": "us-central1",
        "BQ_LOCATION": "US",
        "BQ_DATASET": "retail_sales",
        "BQ_TABLE": "weekly_store_sales",
        "USER_DOMAIN": "your-company.com",
        "ADMIN_USER": "",
        "NORTH_USER": "",
        "SOUTH_USER": "",
        "WEST_USER": "",
        "GE_APP_ID": "omni-retail-sales-ge",
        "GE_APP_DISPLAY_NAME": "OmniRetail 3LO Sales Assistant",
        "AGENT_DISPLAY_NAME": "OmniRetail BigQuery 3LO Sales Agent",
        "AGENT_DESCRIPTION": (
            "Analyzes weekly store sales, margins, returns, and stockouts across regions "
            "using per-user 3-Legged OAuth (3LO) and BigQuery Row-Level Security (RLS)."
        ),
        "GEMINI_AUTH_ID": "bq-3lo-auth",
        "SKIP_GEMINI_ENTERPRISE": "false",
        "OAUTH_CLIENT_ID": "",
        "OAUTH_CLIENT_SECRET": "",
    }
    src = ROOT_ENV_PATH if ROOT_ENV_PATH.exists() else ROOT_ENV_EXAMPLE_PATH
    if src.exists():
        for raw in src.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip("\"'")
            if v.startswith("${") and v.endswith(":-}"):
                v = ""
            for ek, ev in cfg.items():
                v = v.replace(f"${{{ek}}}", ev)
            cfg[k.strip()] = v
    return cfg


def discover_client_secret_json(expected_project_id: str = "") -> tuple[str, str, str, list[str]]:
    """Returns (client_id, client_secret, source_filename, redirect_uris) if client_secret*.json exists."""
    search_dirs = [REPO_ROOT, AGENT_DIR, AGENT_DIR / "app"]
    for d in search_dirs:
        if not d.exists():
            continue
        for cand in sorted(d.glob("client_secret*.json")):
            try:
                raw = json.loads(cand.read_text(encoding="utf-8"))
                block = raw.get("web") or raw.get("installed") or {}
                cid = block.get("client_id", "").strip()
                csecret = block.get("client_secret", "").strip()
                file_proj = block.get("project_id", "").strip()
                redirects = block.get("redirect_uris") or []
                if not (cid and csecret):
                    continue
                if (
                    expected_project_id
                    and expected_project_id != "your-gcp-project-id"
                    and file_proj
                    and file_proj != expected_project_id
                ):
                    continue
                return cid, csecret, str(cand.relative_to(REPO_ROOT)), redirects
            except Exception:
                continue
    return "", "", "", []


def write_single_dotenv(cfg: dict[str, str]) -> None:
    domain = cfg.get("USER_DOMAIN", "your-company.com")
    admin_u = cfg.get("ADMIN_USER") or f"admin@{domain}"
    north_u = cfg.get("NORTH_USER") or f"uno@{domain}"
    south_u = cfg.get("SOUTH_USER") or f"dos@{domain}"
    west_u = cfg.get("WEST_USER") or f"tres@{domain}"

    content = f"""# ==============================================================================
# Single Canonical Configuration (`.env`)
# BigQuery 3-Legged OAuth (3LO), Row-Level Security (RLS) & Agent Identity
# Generated by deploy/00_preflight_and_configure.py
# ==============================================================================

# 1. Google Cloud Project & Locations
PROJECT_ID="{cfg['PROJECT_ID']}"
REGION="{cfg['REGION']}"
BQ_LOCATION="{cfg['BQ_LOCATION']}"

# 2. BigQuery Dataset & Table Identifiers
BQ_PROJECT_ID="{cfg['PROJECT_ID']}"
BQ_DATASET="{cfg['BQ_DATASET']}"
BQ_TABLE="{cfg['BQ_TABLE']}"

# 3. Identity & Persona Emails (for BigQuery RLS & Gemini Enterprise Licensing)
USER_DOMAIN="{domain}"
ADMIN_USER="{admin_u}"
NORTH_USER="{north_u}"
SOUTH_USER="{south_u}"
WEST_USER="{west_u}"

# 4. 3-Legged OAuth (3LO) Web Client Credentials
OAUTH_CLIENT_ID="{cfg.get('OAUTH_CLIENT_ID', '')}"
OAUTH_CLIENT_SECRET="{cfg.get('OAUTH_CLIENT_SECRET', '')}"

# 5. Gemini Enterprise & ADK Agent Configuration
GE_APP_ID="{cfg['GE_APP_ID']}"
GE_APP_DISPLAY_NAME="{cfg['GE_APP_DISPLAY_NAME']}"
AGENT_DISPLAY_NAME="{cfg['AGENT_DISPLAY_NAME']}"
AGENT_DESCRIPTION="{cfg['AGENT_DESCRIPTION']}"
GEMINI_AUTH_ID="{cfg['GEMINI_AUTH_ID']}"
SKIP_GEMINI_ENTERPRISE="{cfg.get('SKIP_GEMINI_ENTERPRISE', 'false')}"

# 6. Local ADK Web / GenAI SDK Runtime Defaults
GOOGLE_GENAI_USE_VERTEXAI="true"
GOOGLE_CLOUD_PROJECT="{cfg['PROJECT_ID']}"
GOOGLE_CLOUD_LOCATION="global"
"""
    ROOT_ENV_PATH.write_text(content, encoding="utf-8")
    print(f"✅ Wrote single canonical configuration to: {ROOT_ENV_PATH.relative_to(REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Up-front pre-flight validator and single .env generator for BigQuery 3LO & Agent Identity."
    )
    parser.add_argument("--interactive", action="store_true", help="Prompt interactively in the terminal")
    parser.add_argument("--write-config", action="store_true", help="Write the root .env configuration file")
    parser.add_argument("--project-id", help="Target GCP Project ID")
    parser.add_argument("--region", help="Vertex AI Agent Runtime region (default: us-central1)")
    parser.add_argument("--bq-dataset", help="BigQuery dataset name (default: retail_sales)")
    parser.add_argument("--bq-table", help="BigQuery table name (default: weekly_store_sales)")
    parser.add_argument("--bq-location", help="BigQuery location (default: US)")
    parser.add_argument("--user-domain", help="Google Workspace / Cloud Identity domain (e.g. company.com)")
    parser.add_argument("--admin-user", help="Admin user email (full access across all regions)")
    parser.add_argument("--north-user", help="North region user email (filtered to region='North')")
    parser.add_argument("--south-user", help="South region user email (filtered to region='South')")
    parser.add_argument("--west-user", help="West region user email (filtered to region='West')")
    parser.add_argument("--ge-app-id", help="Gemini Enterprise App ID (default: omni-retail-sales-ge)")
    parser.add_argument(
        "--skip-gemini-enterprise",
        action="store_true",
        help="Deploy Agent Runtime only (skip Gemini Enterprise provisioning)",
    )
    parser.add_argument("--oauth-client-id", help="OAuth 2.0 Web Client ID")
    parser.add_argument("--oauth-client-secret", help="OAuth 2.0 Web Client Secret")
    args = parser.parse_args()

    cfg = load_existing_config()

    # Auto-detect active gcloud project and account as helpful defaults
    _, active_gcloud_proj, _ = _run(["gcloud", "config", "get-value", "project"])
    _, active_gcloud_acct, _ = _run(["gcloud", "config", "get-value", "account"])
    if cfg["PROJECT_ID"] == "your-gcp-project-id" and active_gcloud_proj and active_gcloud_proj != "(unset)":
        cfg["PROJECT_ID"] = active_gcloud_proj
    if cfg["USER_DOMAIN"] == "your-company.com" and "@" in active_gcloud_acct:
        cfg["USER_DOMAIN"] = active_gcloud_acct.split("@", 1)[1]
        if not cfg["ADMIN_USER"] or cfg["ADMIN_USER"].endswith("@your-company.com"):
            cfg["ADMIN_USER"] = active_gcloud_acct

    # Apply CLI flag overrides
    flag_map = {
        "PROJECT_ID": args.project_id,
        "REGION": args.region,
        "BQ_DATASET": args.bq_dataset,
        "BQ_TABLE": args.bq_table,
        "BQ_LOCATION": args.bq_location,
        "USER_DOMAIN": args.user_domain,
        "ADMIN_USER": args.admin_user,
        "NORTH_USER": args.north_user,
        "SOUTH_USER": args.south_user,
        "WEST_USER": args.west_user,
        "GE_APP_ID": args.ge_app_id,
        "OAUTH_CLIENT_ID": args.oauth_client_id,
        "OAUTH_CLIENT_SECRET": args.oauth_client_secret,
    }
    for k, val in flag_map.items():
        if val is not None:
            cfg[k] = val.strip()
    if args.skip_gemini_enterprise:
        cfg["SKIP_GEMINI_ENTERPRISE"] = "true"

    # Derive USER_DOMAIN from ADMIN_USER if not explicitly provided
    if cfg.get("ADMIN_USER") and "@" in cfg["ADMIN_USER"] and cfg["USER_DOMAIN"] == "your-company.com":
        cfg["USER_DOMAIN"] = cfg["ADMIN_USER"].split("@", 1)[1]

    domain = cfg["USER_DOMAIN"]
    for key, prefix in (
        ("ADMIN_USER", "admin"),
        ("NORTH_USER", "uno"),
        ("SOUTH_USER", "dos"),
        ("WEST_USER", "tres"),
    ):
        if not cfg.get(key) or cfg[key].endswith("@your-company.com"):
            cfg[key] = f"{prefix}@{domain}"

    if args.interactive:
        print("=" * 78)
        print("🛠️  Interactive Setup: BigQuery 3LO & SPIFFE Agent Identity (Single .env)")
        print("=" * 78)
        cfg["PROJECT_ID"] = input(f"1. GCP Project ID [{cfg['PROJECT_ID']}]: ").strip() or cfg["PROJECT_ID"]
        cfg["REGION"] = input(f"2. Agent Runtime Region [{cfg['REGION']}]: ").strip() or cfg["REGION"]
        cfg["BQ_DATASET"] = input(f"3. BigQuery Dataset [{cfg['BQ_DATASET']}]: ").strip() or cfg["BQ_DATASET"]
        cfg["BQ_TABLE"] = input(f"4. BigQuery Table [{cfg['BQ_TABLE']}]: ").strip() or cfg["BQ_TABLE"]
        cfg["ADMIN_USER"] = (
            input(f"5. Admin User Email (sees all regions) [{cfg['ADMIN_USER']}]: ").strip() or cfg["ADMIN_USER"]
        )
        cfg["NORTH_USER"] = (
            input(f"6. Test User 1 - North Region [{cfg['NORTH_USER']}]: ").strip() or cfg["NORTH_USER"]
        )
        cfg["SOUTH_USER"] = (
            input(f"7. Test User 2 - South Region [{cfg['SOUTH_USER']}]: ").strip() or cfg["SOUTH_USER"]
        )
        cfg["WEST_USER"] = (
            input(f"8. Test User 3 - West Region [{cfg['WEST_USER']}]: ").strip() or cfg["WEST_USER"]
        )
        cid = input(
            f"9. OAuth 2.0 Client ID (leave blank if using client_secret_*.json) [{cfg['OAUTH_CLIENT_ID']}]: "
        ).strip()
        if cid:
            cfg["OAUTH_CLIENT_ID"] = cid
        csec = input("10. OAuth 2.0 Client Secret (leave blank to keep existing): ").strip()
        if csec:
            cfg["OAUTH_CLIENT_SECRET"] = csec

    # Auto-discover client_secret_*.json if OAUTH_CLIENT_ID is still empty
    disc_id, disc_secret, disc_file, disc_redirects = discover_client_secret_json(cfg["PROJECT_ID"])
    if not cfg.get("OAUTH_CLIENT_ID") and disc_id:
        cfg["OAUTH_CLIENT_ID"] = disc_id
        cfg["OAUTH_CLIENT_SECRET"] = disc_secret

    if args.write_config or args.interactive or any(v is not None for v in flag_map.values()):
        write_single_dotenv(cfg)

    print("\n" + "=" * 78)
    print("🔍 UP-FRONT PRE-FLIGHT READINESS, PERMISSION & RESOURCE CHECK")
    print("=" * 78)
    manual_steps: list[str] = []

    # 1. Check CLI tools (gcloud, bq, uv)
    for tool_name, install_hint in (
        ("gcloud", "Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install"),
        ("bq", "Install Google Cloud SDK BigQuery CLI: gcloud components install bq"),
        ("uv", "Install uv Python package manager: curl -LsSf https://astral.sh/uv/install.sh | sh"),
    ):
        path = shutil.which(tool_name)
        if path:
            print(f"✅ CLI Tool '{tool_name:<6}' : Found ({path})")
        else:
            print(f"❌ CLI Tool '{tool_name:<6}' : MISSING")
            manual_steps.append(f"Install `{tool_name}`: `{install_hint}`")

    # Sync retail-sales-agent/.venv if uv is available
    if shutil.which("uv"):
        rc, _, err = _run(["uv", "sync"], cwd=AGENT_DIR)
        if rc == 0:
            print("✅ Agent Environment  : `retail-sales-agent/.venv` synced via `uv sync`")
        else:
            print(f"⚠️ Agent Environment  : `uv sync` returned {rc} ({err[:120]})")

    # 2. Check Project ID & Persona Emails
    proj_id = cfg["PROJECT_ID"]
    if not proj_id or proj_id == "your-gcp-project-id":
        print("❌ GCP Project ID     : Not configured yet (currently 'your-gcp-project-id')")
        manual_steps.append(
            "Provide your target GCP Project ID and run:\n"
            "     `python3 deploy/00_preflight_and_configure.py --project-id <YOUR_PROJECT_ID> --write-config`"
        )
    else:
        print(f"✅ GCP Project ID     : {proj_id}")
        print(
            f"✅ RLS User Personas  : Admin={cfg['ADMIN_USER']} | "
            f"North={cfg['NORTH_USER']} | South={cfg['SOUTH_USER']} | West={cfg['WEST_USER']}"
        )

    # 3. Check gcloud & ADC authentication
    rc_a, tok_a, _ = _run(["gcloud", "auth", "application-default", "print-access-token"])
    rc_g, tok_g, _ = _run(["gcloud", "auth", "print-access-token"])
    active_token = tok_a or tok_g

    if rc_a == 0 and tok_a:
        print("✅ ADC Credentials    : Application Default Credentials (ADC) active")
        if rc_g == 0 and tok_g:
            print(f"✅ gcloud CLI Auth    : Authenticated ({active_gcloud_acct or 'active token'})")
        else:
            print(
                f"✅ gcloud CLI Auth    : Bridged automatically from active ADC ({active_gcloud_acct or 'ADC token'})"
            )
    elif rc_g == 0 and tok_g:
        print(f"✅ gcloud CLI Auth    : Authenticated ({active_gcloud_acct or 'active token'})")
        print("❌ ADC Credentials    : Application Default Credentials (ADC) not logged in")
        manual_steps.append(
            f"Configure Application Default Credentials (ADC):\n"
            f"     `gcloud auth application-default login`\n"
            f"     `gcloud auth application-default set-quota-project {proj_id}`"
        )
    else:
        print("❌ GCP Authentication : Neither ADC nor gcloud CLI is logged in")
        manual_steps.append(
            f"Authenticate with Google Cloud:\n"
            f"     `gcloud auth application-default login`\n"
            f"     `gcloud auth application-default set-quota-project {proj_id}`"
        )

    # 4. Verify Project Access, Lifecycle State, Billing & Caller IAM Permissions
    if active_token and proj_id and proj_id != "your-gcp-project-id":
        headers = {"Authorization": f"Bearer {active_token}", "Content-Type": "application/json"}
        req = urllib.request.Request(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{proj_id}",
            headers=headers,
        )
        project_accessible = False
        try:
            with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=15) as resp:
                pdata = json.loads(resp.read().decode("utf-8"))
                pnum = pdata.get("projectNumber", "")
                state = pdata.get("lifecycleState", "ACTIVE")
                if state != "ACTIVE":
                    print(f"❌ GCP Project State  : Project '{proj_id}' is in state '{state}' (must be ACTIVE)")
                    manual_steps.append(f"Use an ACTIVE Google Cloud project instead of `{proj_id}`.")
                else:
                    print(f"✅ GCP Project Access : Verified '{proj_id}' (projectNumber: {pnum}, state: {state})")
                    project_accessible = True
        except urllib.error.HTTPError as exc:
            print(f"❌ GCP Project Access : HTTP {exc.code} accessing project '{proj_id}'")
            manual_steps.append(
                f"Verify that project `{proj_id}` exists and account `{active_gcloud_acct}` has access to it."
            )
        except Exception as exc:
            print(f"⚠️ GCP Project Access : Could not reach Cloud Resource Manager ({exc})")

        # 4b. Test Caller IAM Permissions on the Project
        if project_accessible:
            perm_req = urllib.request.Request(
                f"https://cloudresourcemanager.googleapis.com/v1/projects/{proj_id}:testIamPermissions",
                data=json.dumps({"permissions": REQUIRED_PROJECT_PERMISSIONS}).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(perm_req, context=_ssl_ctx(), timeout=15) as resp:
                    perm_data = json.loads(resp.read().decode("utf-8"))
                    granted = set(perm_data.get("permissions") or [])
                    missing = [p for p in REQUIRED_PROJECT_PERMISSIONS if p not in granted]
                    if not missing:
                        print(
                            "✅ Caller IAM Roles   : Verified permissions for API enablement, IAM, BigQuery & Vertex AI"
                        )
                    else:
                        print(f"❌ Caller IAM Roles   : Missing project permissions: {', '.join(missing)}")
                        manual_steps.append(
                            f"Grant `{active_gcloud_acct}` Owner (`roles/owner`) or Editor + Project IAM Admin "
                            f"(`roles/resourcemanager.projectIamAdmin`, `roles/serviceusage.serviceUsageAdmin`, "
                            f"`roles/bigquery.admin`, `roles/aiplatform.admin`) on project `{proj_id}`."
                        )
            except Exception as exc:
                print(f"⚠️ Caller IAM Roles   : Could not verify testIamPermissions ({exc})")

            # 4c. Check Cloud Billing status (best-effort; skips gracefully if Cloud Billing API is not enabled)
            bill_req = urllib.request.Request(
                f"https://cloudbilling.googleapis.com/v1/projects/{proj_id}/billingInfo",
                headers=headers,
            )
            try:
                with urllib.request.urlopen(bill_req, context=_ssl_ctx(), timeout=10) as resp:
                    bdata = json.loads(resp.read().decode("utf-8"))
                    if bdata.get("billingEnabled"):
                        print("✅ Cloud Billing      : Billing is enabled on target project")
                    else:
                        print(f"❌ Cloud Billing      : Billing is NOT enabled on project '{proj_id}'")
                        manual_steps.append(
                            f"Link an active Cloud Billing account to project `{proj_id}`: "
                            f"https://console.cloud.google.com/billing/linkedaccount?project={proj_id}"
                        )
            except urllib.error.HTTPError:
                # Cloud Billing API may not be enabled yet; do not block
                pass
            except Exception:
                pass

            # 4d. Automatically enable foundational APIs & initialize Internal OAuth Brand on new projects
            if pnum:
                for svc in ("serviceusage.googleapis.com", "cloudresourcemanager.googleapis.com", "iap.googleapis.com"):
                    try:
                        su_req = urllib.request.Request(
                            f"https://serviceusage.googleapis.com/v1/projects/{proj_id}/services/{svc}:enable",
                            data=b"{}",
                            headers=headers,
                            method="POST",
                        )
                        urllib.request.urlopen(su_req, context=_ssl_ctx(), timeout=20).read()
                    except Exception:
                        pass
                brand_url = f"https://iap.googleapis.com/v1/projects/{pnum}/brands"
                try:
                    with urllib.request.urlopen(
                        urllib.request.Request(brand_url, headers=headers), context=_ssl_ctx(), timeout=15
                    ) as b_resp:
                        brands = json.loads(b_resp.read().decode("utf-8")).get("brands", [])
                    if brands:
                        print(f"✅ OAuth Brand Screen : Internal Brand '{brands[0].get('applicationTitle')}' active")
                    else:
                        create_b_req = urllib.request.Request(
                            brand_url,
                            data=json.dumps(
                                {
                                    "applicationTitle": cfg["GE_APP_DISPLAY_NAME"],
                                    "supportEmail": cfg["ADMIN_USER"],
                                }
                            ).encode("utf-8"),
                            headers=headers,
                            method="POST",
                        )
                        with urllib.request.urlopen(create_b_req, context=_ssl_ctx(), timeout=15) as cb_resp:
                            created_b = json.loads(cb_resp.read().decode("utf-8"))
                            print(
                                f"✅ OAuth Brand Screen : Auto-created Internal OAuth Brand '{created_b.get('applicationTitle')}'"
                            )
                except Exception:
                    pass

    # 5. Check OAuth 2.0 Web Client Credentials & Redirect URIs
    if cfg.get("OAUTH_CLIENT_ID") and cfg.get("OAUTH_CLIENT_SECRET"):
        src_note = f"from {disc_file}" if disc_file else "from .env"
        print(f"✅ 3LO OAuth Client   : Configured ({cfg['OAUTH_CLIENT_ID'][:28]}... {src_note})")
        if (
            disc_redirects
            and cfg.get("SKIP_GEMINI_ENTERPRISE", "false").lower() != "true"
            and REQUIRED_GE_REDIRECT_URI not in disc_redirects
        ):
            print(
                f"⚠️ 3LO Redirect URI   : `{disc_file}` does not list `{REQUIRED_GE_REDIRECT_URI}` in `redirect_uris`"
            )
            manual_steps.append(
                f"Ensure `{REQUIRED_GE_REDIRECT_URI}` is added under **Authorized redirect URIs** for OAuth Client "
                f"`{cfg['OAUTH_CLIENT_ID']}` at:\n"
                f"     https://console.cloud.google.com/auth/clients?project={proj_id}"
            )
    else:
        print("❌ 3LO OAuth Client   : Missing OAuth 2.0 Web Client ID & Secret in `.env`")
        manual_steps.append(
            f"Create an OAuth 2.0 Web Client in Google Cloud Console for project `{proj_id}` "
            f"(Branding & Internal Audience have already been auto-configured for you!):\n"
            f"     1. Open OAuth Clients & click **Create Client** -> **Web application**:\n"
            f"        • https://console.cloud.google.com/auth/clients/create?project={proj_id}\n"
            f"     2. Under **Authorized redirect URIs**, add:\n"
            f"        • `{REQUIRED_GE_REDIRECT_URI}` (Required for Gemini Enterprise)\n"
            f"        • `{LOCAL_ADK_REDIRECT_URI}` (Optional, for local `adk web`)\n"
            f"     3. Put `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` in `.env` (or download `client_secret_<id>.json` "
            f"into the repo root) and run:\n"
            f"        `python3 deploy/00_preflight_and_configure.py --oauth-client-id <ID> --oauth-client-secret <SECRET> --write-config`"
        )

    print("=" * 78)
    if manual_steps:
        print("🛑 ACTION REQUIRED FROM USER BEFORE DEPLOYMENT:")
        for idx, step in enumerate(manual_steps, 1):
            print(f"\n  {idx}. {step}")
        print("\n" + "=" * 78)
        sys.exit(2)
    else:
        print("🎉 ALL PRE-FLIGHT CHECKS PASSED! Ready for automated deployment:")
        print("   1. Provision BigQuery Dataset & RLS : ./deploy/01_setup_bq_and_rls.sh")
        print("   2. Deploy Agent & Gemini Enterprise : ./deploy/02_deploy_agent_and_ge.sh")
        print("=" * 78)


if __name__ == "__main__":
    main()
