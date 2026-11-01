#!/usr/bin/env python3
"""
Discovery Engine & Gemini Enterprise Automation for the BigQuery 3LO Demo.

Modes:
  1. Pre-deploy (`--prepare`, default):
     - Ensures active project-local `licenseConfig` and assigns licenses to all demo personas.
     - Configures `AclConfig` (`idpType=GSUITE`) and grants `roles/discoveryengine.user` + `viewer`.
     - Ensures backing `DataStore` and Gemini Enterprise `Engine` (App).
     - Verifies Internal OAuth Brand, discovers/validates `client_secret*.json`, creates/updates
       Discovery Engine OAuth `Authorization` (`bq-3lo-auth`), and writes `app/oauth_config.json`.
  2. Post-publish (`--finalize-agent-display-name <NAME>`):
     - Patches `sharingConfig: {"scope": "ALL_USERS"}` and `authorizationConfig` on the registered
       ADK agent, fetches `widgetConfigs/default_search_widget_config`, and prints the Web App URL.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_GE_REDIRECT_URI = "https://vertexaisearch.cloud.google.com/oauth-redirect"


def _get_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    ctx = ssl.create_default_context()
    return ctx if ctx.get_ca_certs() else ssl._create_unverified_context()


SSL_CTX = _get_ssl_context()


def _gcloud_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE",
        "CLOUDSDK_CONTEXT_AWARE_USE_ECP_HTTP_PROXY",
        "CLOUDSDK_CONTEXT_AWARE_USE_MTLS_FOR_GRPC",
    ):
        env.setdefault(key, "false")
    env.setdefault("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")
    metrics = env.get("CLOUDSDK_METRICS_ENVIRONMENT", "")
    if "datacloud.antigravity" not in metrics:
        env["CLOUDSDK_METRICS_ENVIRONMENT"] = f"{metrics} datacloud.antigravity".strip()
    return env


def get_access_token() -> str:
    if env_tok := os.environ.get("CLOUDSDK_AUTH_ACCESS_TOKEN", "").strip():
        return env_tok
    env = _gcloud_env()
    for cmd in (
        ["gcloud", "auth", "print-access-token"],
        ["gcloud", "auth", "application-default", "print-access-token"],
    ):
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        if creds.token:
            return creds.token
    except Exception:
        pass
    sys.exit("❌ Failed to obtain Google Cloud access token via gcloud or ADC.")


def api_request(
    method: str,
    url: str,
    token: str,
    quota_project: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-goog-user-project": quota_project,
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        try:
            return err.code, json.loads(raw)
        except Exception:
            return err.code, {"raw_error": raw}


def get_project_number(project_id: str, token: str) -> str:
    status, data = api_request(
        "GET",
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}",
        token,
        project_id,
    )
    if status != 200 or "projectNumber" not in data:
        sys.exit(f"❌ Failed to resolve project number for '{project_id}' ({status}): {data}")
    return str(data["projectNumber"])


def ensure_active_license(
    project_number: str,
    project_id: str,
    location: str,
    user_emails: list[str],
    token: str,
) -> None:
    print(f"🪪 Checking Gemini Enterprise license configuration in '{project_id}' ({project_number})...")
    loc_base = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}/locations/{location}"
    us_status, _ = api_request("GET", f"{loc_base}/userStores/default_user_store", token, project_id)
    if us_status == 404:
        api_request(
            "POST",
            f"{loc_base}/userStores?userStoreId=default_user_store",
            token,
            project_id,
            {"enableLicenseAutoRegister": True},
        )
        time.sleep(2)

    lic_url = f"{loc_base}/licenseConfigs"
    status, data = api_request("GET", lic_url, token, project_id)
    active_license = next(
        (lic.get("name") for lic in data.get("licenseConfigs", []) if status == 200 and lic.get("state") == "ACTIVE"),
        None,
    )
    if active_license:
        print(f"✅ Found active LicenseConfig: {active_license}")
    else:
        print("🛠️ Provisioning project-local 'free_trial_gemini' LicenseConfig...")
        next_year = datetime.date.today() + datetime.timedelta(days=365)
        c_status, c_data = api_request(
            "POST",
            f"{lic_url}?licenseConfigId=free_trial_gemini",
            token,
            project_id,
            {
                "licenseCount": 50,
                "subscriptionTerm": "SUBSCRIPTION_TERM_ONE_YEAR",
                "subscriptionTier": "SUBSCRIPTION_TIER_SEARCH_AND_ASSISTANT",
                "freeTrial": True,
                "endDate": {"year": next_year.year, "month": next_year.month, "day": next_year.day},
            },
        )
        if c_status in (200, 201) and "name" in c_data:
            active_license = c_data["name"]
            print(f"✅ Created LicenseConfig: {active_license}")
        else:
            print(f"⚠️ Could not create LicenseConfig ({c_status}): {json.dumps(c_data)}")
            return

    api_request(
        "PATCH",
        f"{loc_base}/userStores/default_user_store?updateMask=defaultLicenseConfig,enableLicenseAutoRegister",
        token,
        project_id,
        {"defaultLicenseConfig": active_license, "enableLicenseAutoRegister": True},
    )
    if user_emails:
        b_status, b_data = api_request(
            "POST",
            f"{loc_base}/userStores/default_user_store:batchUpdateUserLicenses",
            token,
            project_id,
            {
                "inlineSource": {
                    "userLicenses": [
                        {
                            "userPrincipal": email,
                            "licenseConfig": active_license,
                            "licenseAssignmentState": "ASSIGNED",
                        }
                        for email in user_emails
                    ],
                    "updateMask": "licenseConfig,licenseAssignmentState",
                }
            },
        )
        if b_status == 200:
            print(f"✅ Assigned active Gemini Enterprise licenses to: {', '.join(user_emails)}")
        else:
            print(f"⚠️ Warning assigning user licenses ({b_status}): {json.dumps(b_data)}")


def ensure_ge_user_access(
    project_number: str,
    project_id: str,
    location: str,
    user_emails: list[str],
    token: str,
) -> None:
    acl_name = f"projects/{project_number}/locations/{location}/aclConfig"
    a_status, _ = api_request(
        "PATCH",
        f"https://discoveryengine.googleapis.com/v1alpha/{acl_name}",
        token,
        project_id,
        {"name": acl_name, "idpConfig": {"idpType": "GSUITE"}},
    )
    if a_status == 200:
        print(f"✅ Configured Discovery Engine AclConfig idpType=GSUITE in '{location}'")
    if not user_emails:
        return

    crm_base = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}"
    i_status, policy = api_request("POST", f"{crm_base}:getIamPolicy", token, project_id, {})
    if i_status != 200:
        print(f"⚠️ Warning fetching project IAM policy ({i_status})")
        return

    members = [f"user:{e}" for e in user_emails]
    changed = False
    for role in ("roles/discoveryengine.user", "roles/discoveryengine.viewer"):
        binding = next((b for b in policy.get("bindings", []) if b.get("role") == role), None)
        if binding is None:
            policy.setdefault("bindings", []).append({"role": role, "members": list(members)})
            changed = True
        else:
            for member in members:
                if member not in binding["members"]:
                    binding["members"].append(member)
                    changed = True

    if changed:
        s_status, _ = api_request("POST", f"{crm_base}:setIamPolicy", token, project_id, {"policy": policy})
        if s_status == 200:
            print(f"✅ Granted Discovery Engine user & viewer roles to: {', '.join(user_emails)}")
    else:
        print(f"✅ Verified Discovery Engine user & viewer roles for: {', '.join(user_emails)}")


def ensure_ge_engine(
    project_number: str,
    project_id: str,
    location: str,
    engine_id: str,
    display_name: str,
    token: str,
) -> str:
    col_base = (
        f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}"
        f"/locations/{location}/collections/default_collection"
    )
    engine_url = f"{col_base}/engines/{engine_id}"
    status, data = api_request("GET", engine_url, token, project_id)
    if status == 200 and "name" in data:
        print(f"✅ Gemini Enterprise App '{engine_id}' already exists: {data['name']}")
        return data["name"]

    ds_id = f"{engine_id}-ds"
    ds_url = f"{col_base}/dataStores/{ds_id}"
    if api_request("GET", ds_url, token, project_id)[0] != 200:
        print(f"📦 Creating backing DataStore '{ds_id}'...")
        api_request(
            "POST",
            f"{col_base}/dataStores?dataStoreId={ds_id}",
            token,
            project_id,
            {
                "displayName": f"{display_name} Data Store",
                "industryVertical": "GENERIC",
                "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
                "contentConfig": "NO_CONTENT",
            },
        )
        for _ in range(10):
            time.sleep(2)
            if api_request("GET", ds_url, token, project_id)[0] == 200:
                break

    print(f"🚀 Creating Gemini Enterprise App '{display_name}' (ID: {engine_id})...")
    status, data = api_request(
        "POST",
        f"{col_base}/engines?engineId={engine_id}",
        token,
        project_id,
        {
            "displayName": display_name,
            "solutionType": "SOLUTION_TYPE_SEARCH",
            "industryVertical": "GENERIC",
            "appType": "APP_TYPE_INTRANET",
            "dataStoreIds": [ds_id],
            "searchEngineConfig": {
                "searchTier": "SEARCH_TIER_ENTERPRISE",
                "searchAddOns": ["SEARCH_ADD_ON_LLM"],
            },
        },
    )
    if status not in (200, 201):
        sys.exit(f"❌ Failed to create Gemini Enterprise App ({status}): {json.dumps(data)}")

    for _ in range(15):
        time.sleep(3)
        s, d = api_request("GET", engine_url, token, project_id)
        if s == 200 and "name" in d:
            print(f"✅ Gemini Enterprise App ready: {d['name']}")
            return d["name"]
    return f"projects/{project_number}/locations/{location}/collections/default_collection/engines/{engine_id}"


def ensure_oauth_brand(
    project_number: str, project_id: str, support_email: str, app_title: str, token: str
) -> None:
    brands_url = f"https://iap.googleapis.com/v1/projects/{project_number}/brands"
    status, data = api_request("GET", brands_url, token, project_id)
    if status == 200 and data.get("brands"):
        print(f"✅ Internal OAuth Brand verified: {data['brands'][0].get('name')}")
        return
    if support_email:
        c_status, c_data = api_request(
            "POST",
            brands_url,
            token,
            project_id,
            {"supportEmail": support_email, "applicationTitle": app_title},
        )
        if c_status in (200, 201):
            print(f"✅ Created Internal OAuth Brand: {c_data.get('name')}")


def discover_oauth_credentials(
    client_id: str, client_secret: str, expected_project_id: str = ""
) -> tuple[str, str]:
    if client_id and client_secret:
        return client_id, client_secret
    search_dirs = [REPO_ROOT, REPO_ROOT / "retail-sales-agent", REPO_ROOT / "retail-sales-agent" / "app"]
    candidates = sorted(p for d in search_dirs for p in d.glob("client_secret*.json"))
    for cand in candidates:
        try:
            raw = json.loads(cand.read_text(encoding="utf-8"))
            block = raw.get("web") or raw.get("installed") or {}
            cid, csecret = block.get("client_id", ""), block.get("client_secret", "")
            file_proj = block.get("project_id", "")
            redirects = block.get("redirect_uris", [])
            if not (cid and csecret):
                continue
            if expected_project_id and file_proj and file_proj != expected_project_id:
                print(f"⚠️ Skipping {cand.name}: project_id '{file_proj}' != '{expected_project_id}'")
                continue
            if REQUIRED_GE_REDIRECT_URI not in redirects:
                print(
                    f"⚠️ Warning in {cand.name}: redirect_uris {redirects} is missing '{REQUIRED_GE_REDIRECT_URI}'!"
                )
            print(f"🔑 Discovered OAuth 2.0 Web Client from {cand.name} (project: {file_proj or expected_project_id})")
            return cid, csecret
        except Exception:
            continue
    return "", ""


def ensure_ge_oauth_authorization(
    project_number: str,
    project_id: str,
    location: str,
    auth_id: str,
    client_id: str,
    client_secret: str,
    token: str,
) -> str:
    loc_base = f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}/locations/{location}"
    auth_res = f"projects/{project_number}/locations/{location}/authorizations/{auth_id}"
    auth_uri = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "scope": "https://www.googleapis.com/auth/bigquery",
            "include_granted_scopes": "true",
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    payload = {
        "name": auth_res,
        "serverSideOauth2": {
            "clientId": client_id,
            "clientSecret": client_secret,
            "authorizationUri": auth_uri,
            "tokenUri": "https://oauth2.googleapis.com/token",
        },
    }
    get_url = f"{loc_base}/authorizations/{auth_id}"
    if api_request("GET", get_url, token, project_id)[0] == 200:
        status, data = api_request("PATCH", f"{get_url}?updateMask=serverSideOauth2", token, project_id, payload)
        if status in (200, 201):
            print(f"✅ Updated OAuth Authorization: {auth_res}")
            return auth_res
        print(f"⚠️ Warning updating Authorization ({status}): {json.dumps(data)}")
        return auth_res

    status, data = api_request("POST", f"{loc_base}/authorizations?authorizationId={auth_id}", token, project_id, payload)
    if status in (200, 201):
        print(f"✅ Created OAuth Authorization: {auth_res}")
        return auth_res
    print(f"⚠️ Warning creating OAuth Authorization ({status}): {json.dumps(data)}")
    return ""


def bind_agent_identity(
    project_id: str,
    agent_runtime_id: str,
    token: str,
) -> str:
    """
    Inspects the deployed Reasoning Engine's `spec.effectiveIdentity` (SPIFFE principal)
    and ensures Plane 1 least-privilege IAM roles are bound on the project (with zero BigQuery roles).
    """
    parts = agent_runtime_id.strip("/").split("/")
    region = parts[3] if len(parts) >= 4 and parts[2] == "locations" else "us-central1"
    re_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/{agent_runtime_id}"
    status, data = api_request("GET", re_url, token, project_id)
    if status != 200:
        print(f"⚠️ Warning fetching Reasoning Engine metadata ({status}): {json.dumps(data)}")
        return ""

    spec = data.get("spec", {})
    identity_type = spec.get("identityType", "SERVICE_ACCOUNT")
    effective_identity = spec.get("effectiveIdentity", "")
    if not effective_identity:
        print(f"ℹ️  Reasoning Engine '{agent_runtime_id}' uses identityType={identity_type} (no SPIFFE effectiveIdentity).")
        return ""

    principal = (
        effective_identity
        if effective_identity.startswith("principal://")
        else f"principal://{effective_identity}"
    )
    print(f"🪪 Verified Reasoning Engine Identity Type : {identity_type}")
    print(f"🔐 SPIFFE Effective Identity Principal    : {principal}")

    plane1_roles = (
        "roles/aiplatform.user",
        "roles/logging.logWriter",
        "roles/telemetry.writer",
        "roles/monitoring.metricWriter",
        "roles/serviceusage.serviceUsageConsumer",
    )
    crm_base = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}"
    i_status, policy = api_request("POST", f"{crm_base}:getIamPolicy", token, project_id, {})
    if i_status != 200:
        print(f"⚠️ Warning fetching project IAM policy ({i_status})")
        return principal

    changed = False
    for role in plane1_roles:
        binding = next((b for b in policy.get("bindings", []) if b.get("role") == role), None)
        if binding is None:
            policy.setdefault("bindings", []).append({"role": role, "members": [principal]})
            changed = True
        elif principal not in binding.get("members", []):
            binding.setdefault("members", []).append(principal)
            changed = True

    if changed:
        s_status, s_data = api_request("POST", f"{crm_base}:setIamPolicy", token, project_id, {"policy": policy})
        if s_status == 200:
            print(f"✅ Granted Plane 1 least-privilege IAM roles ({', '.join(plane1_roles)}) to {principal}")
        else:
            print(f"⚠️ Warning setting Plane 1 IAM policy ({s_status}): {json.dumps(s_data)}")
    else:
        print(f"✅ Verified Plane 1 least-privilege IAM roles already bound to {principal}")
    return principal


def finalize_published_agent(
    project_number: str,
    project_id: str,
    location: str,
    engine_id: str,
    auth_id: str,
    agent_display_name: str,
    target_agent_runtime_id: str,
    token: str,
) -> None:
    engine_res = f"projects/{project_number}/locations/{location}/collections/default_collection/engines/{engine_id}"
    auth_res = ""
    if auth_id:
        cand_auth = f"projects/{project_number}/locations/{location}/authorizations/{auth_id}"
        if api_request("GET", f"https://discoveryengine.googleapis.com/v1alpha/{cand_auth}", token, project_id)[0] == 200:
            auth_res = cand_auth

    agents_url = f"https://discoveryengine.googleapis.com/v1alpha/{engine_res}/assistants/default_assistant/agents"
    status, agents_data = api_request("GET", agents_url, token, project_id)
    if status == 200:
        all_agents = agents_data.get("agents", [])
        matching = [
            ag for ag in all_agents
            if ag.get("displayName") == agent_display_name
            or (
                auth_res
                and auth_res in (ag.get("authorizationConfig", {}).get("toolAuthorizations") or [])
            )
        ]
        # Prefer the registration that already points to target_agent_runtime_id; otherwise pick the newest/first
        chosen = None
        if target_agent_runtime_id:
            for ag in matching:
                re_name = (
                    ag.get("adkAgentDefinition", {})
                    .get("provisionedReasoningEngine", {})
                    .get("reasoningEngine", "")
                )
                if re_name == target_agent_runtime_id:
                    chosen = ag
                    break
        if chosen is None and matching:
            chosen = matching[-1]

        if chosen is not None:
            ag_name = chosen["name"]
            adk_def = dict(chosen.get("adkAgentDefinition", {}))
            if target_agent_runtime_id:
                adk_def.setdefault("provisionedReasoningEngine", {})["reasoningEngine"] = target_agent_runtime_id
            patch_body = {
                "displayName": agent_display_name or chosen.get("displayName", ""),
                "description": chosen.get("description", ""),
                "adkAgentDefinition": adk_def,
                "sharingConfig": {"scope": "ALL_USERS"},
            }
            masks = [" displayName".strip(), "sharingConfig", "adkAgentDefinition"]
            if auth_res:
                patch_body["authorizationConfig"] = {"toolAuthorizations": [auth_res]}
                masks.append("authorizationConfig")
            p_status, p_data = api_request(
                "PATCH",
                f"https://discoveryengine.googleapis.com/v1alpha/{ag_name}?updateMask={','.join(masks)}",
                token,
                project_id,
                patch_body,
            )
            if p_status == 200:
                print(
                    f"   ✅ Agent shared with ALL_USERS & 3LO Auth verified: {ag_name} -> {target_agent_runtime_id or 'current'}",
                    file=sys.stderr,
                )
            else:
                print(f"   ⚠️ Warning patching agent ({p_status}): {json.dumps(p_data)}", file=sys.stderr)

            # Remove any duplicate registrations with the same displayName so only 1 active agent exists in GE
            for dup in matching:
                if dup["name"] != ag_name:
                    d_status, _ = api_request(
                        "DELETE",
                        f"https://discoveryengine.googleapis.com/v1alpha/{dup['name']}",
                        token,
                        project_id,
                    )
                    if d_status in (200, 204):
                        print(f"   🧹 Removed duplicate GE agent registration: {dup['name']}", file=sys.stderr)

    widget_url = f"https://discoveryengine.googleapis.com/v1alpha/{engine_res}/widgetConfigs/default_search_widget_config"
    w_status, w_data = api_request("GET", widget_url, token, project_id)
    if w_status == 200 and w_data.get("configId"):
        print(f"https://vertexaisearch.cloud.google.com/home/cid/{w_data['configId']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision Gemini Enterprise App, Licenses, 3LO Auth, Agent Identity IAM, and Agent Sharing."
    )
    parser.add_argument("--prepare", action="store_true", help="Run pre-deploy setup (default mode)")
    parser.add_argument(
        "--bind-agent-identity",
        default="",
        help="Verify/bind Plane 1 SPIFFE Agent Identity IAM roles for the given Reasoning Engine resource name",
    )
    parser.add_argument(
        "--finalize-agent-display-name",
        default="",
        help="Post-publish mode: patch ALL_USERS sharing + 3LO auth on agent and print Web App URL",
    )
    parser.add_argument(
        "--agent-runtime-id",
        default="",
        help="Optional Reasoning Engine resource name to bind in Gemini Enterprise during finalize",
    )
    parser.add_argument("--project-id", required=True, help="GCP Project ID")
    parser.add_argument("--location", default="global", help="Discovery Engine location (default: global)")
    parser.add_argument("--engine-id", default="omni-retail-sales-ge", help="Gemini Enterprise App Engine ID")
    parser.add_argument("--display-name", default="OmniRetail 3LO Sales Assistant", help="Gemini Enterprise App Display Name")
    parser.add_argument("--user-emails", default="", help="Comma-separated user emails to license and grant access")
    parser.add_argument("--auth-id", default="bq-3lo-auth", help="Discovery Engine OAuth Authorization ID")
    parser.add_argument("--bq-dataset", default="retail_sales", help="BigQuery dataset ID for oauth_config.json")
    parser.add_argument("--bq-table", default="weekly_store_sales", help="BigQuery table ID for oauth_config.json")
    parser.add_argument("--bq-location", default="US", help="BigQuery location for oauth_config.json")
    parser.add_argument("--oauth-client-id", default="", help="Optional OAuth 2.0 Client ID")
    parser.add_argument("--oauth-client-secret", default="", help="Optional OAuth 2.0 Client Secret")
    parser.add_argument(
        "--agent-oauth-config",
        default=str(REPO_ROOT / "retail-sales-agent" / "app" / "oauth_config.json"),
        help="Path to write packaged oauth_config.json for Agent Runtime",
    )
    parser.add_argument("--output-json", default="", help="Optional path to write metadata JSON")
    parser.add_argument("--output-env", default="", help="Optional path to write sourceable bash variables")
    args = parser.parse_args()

    token = get_access_token()
    project_number = get_project_number(args.project_id, token)

    if args.bind_agent_identity:
        bind_agent_identity(
            project_id=args.project_id,
            agent_runtime_id=args.bind_agent_identity,
            token=token,
        )
        return

    if args.finalize_agent_display_name:
        finalize_published_agent(
            project_number=project_number,
            project_id=args.project_id,
            location=args.location,
            engine_id=args.engine_id,
            auth_id=args.auth_id,
            agent_display_name=args.finalize_agent_display_name,
            target_agent_runtime_id=args.agent_runtime_id,
            token=token,
        )
        return

    emails = [e.strip() for e in args.user_emails.split(",") if e.strip()]
    ensure_active_license(project_number, args.project_id, args.location, emails, token)
    ensure_ge_user_access(project_number, args.project_id, args.location, emails, token)
    engine_name = ensure_ge_engine(
        project_number, args.project_id, args.location, args.engine_id, args.display_name, token
    )
    ensure_oauth_brand(project_number, args.project_id, emails[0] if emails else "", args.display_name, token)

    cid, csecret = discover_oauth_credentials(args.oauth_client_id, args.oauth_client_secret, args.project_id)
    if args.agent_oauth_config:
        cfg_path = Path(args.agent_oauth_config)
        cfg_path.write_text(
            json.dumps(
                {
                    "PROJECT_ID": args.project_id,
                    "BQ_DATASET": args.bq_dataset,
                    "BQ_TABLE": args.bq_table,
                    "BQ_LOCATION": args.bq_location,
                    "GEMINI_AUTH_ID": args.auth_id,
                    "OAUTH_CLIENT_ID": cid,
                    "OAUTH_CLIENT_SECRET": csecret,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"✅ Wrote agent config to {cfg_path.relative_to(REPO_ROOT) if cfg_path.is_relative_to(REPO_ROOT) else cfg_path}")

    auth_resource_name = ""
    if cid and csecret:
        auth_resource_name = ensure_ge_oauth_authorization(
            project_number, args.project_id, args.location, args.auth_id, cid, csecret, token
        )
    else:
        print(
            f"ℹ️  No OAuth 2.0 Web Client found for project '{args.project_id}'.\n"
            f"   👉 Create one at: https://console.cloud.google.com/auth/clients?project={args.project_id}\n"
            f"   👉 Authorized redirect URI: {REQUIRED_GE_REDIRECT_URI}"
        )

    out = {
        "project_id": args.project_id,
        "project_number": project_number,
        "engine_name": engine_name,
        "authorization_id": auth_resource_name,
        "oauth_configured": bool(cid and csecret),
    }
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    if args.output_env:
        env_lines = [
            f"PROJECT_NUMBER={shlex.quote(project_number)}",
            f"GE_ENGINE_RESOURCE={shlex.quote(engine_name)}",
            f"GE_AUTH_RESOURCE={shlex.quote(auth_resource_name)}",
        ]
        Path(args.output_env).write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    print(f"📌 Gemini Enterprise App Resource Name: {engine_name}")
    if auth_resource_name:
        print(f"📌 Gemini Enterprise 3LO Authorization: {auth_resource_name}")


if __name__ == "__main__":
    main()
