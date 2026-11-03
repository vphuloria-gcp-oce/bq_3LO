# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
from pathlib import Path
from typing import Optional

import google.auth
import google.auth.credentials
import google.oauth2.credentials
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.integrations.bigquery import BigQueryCredentialsConfig, BigQueryToolset
from google.adk.integrations.bigquery.config import BigQueryToolConfig, WriteMode
from google.adk.models import Gemini
from google.adk.tools._google_credentials import GoogleCredentialsManager
from google.adk.tools.tool_context import ToolContext
from google.genai import types

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent.parent


def _parse_dotenv(dotenv_path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not dotenv_path.exists():
        return parsed
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        parsed[k.strip()] = v.strip().strip("\"'")
    return parsed


def _load_settings() -> dict[str, str]:
    """Loads BigQuery and 3LO OAuth settings from container env vars or the root .env file."""
    file_cfg: dict[str, str] = {}
    for candidate in (REPO_ROOT / ".env", APP_DIR.parent / ".env"):
        if candidate.exists():
            file_cfg.update(_parse_dotenv(candidate))

    # Vertex AI Reasoning Engine sets GOOGLE_CLOUD_PROJECT to a numeric project number; ignore numeric IDs for SQL.
    env_proj = (
        os.environ.get("BQ_PROJECT_ID")
        or os.environ.get("PROJECT_ID")
        or file_cfg.get("BQ_PROJECT_ID")
        or file_cfg.get("PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    )
    if env_proj.isdigit() or env_proj == "your-gcp-project-id":
        env_proj = ""

    settings = {
        "PROJECT_ID": env_proj or "your-gcp-project-id",
        "BQ_DATASET": os.environ.get("BQ_DATASET") or file_cfg.get("BQ_DATASET") or "retail_sales",
        "BQ_TABLE": os.environ.get("BQ_TABLE") or file_cfg.get("BQ_TABLE") or "weekly_store_sales",
        "BQ_LOCATION": os.environ.get("BQ_LOCATION") or file_cfg.get("BQ_LOCATION") or "US",
        "GEMINI_AUTH_ID": os.environ.get("GEMINI_AUTH_ID") or file_cfg.get("GEMINI_AUTH_ID") or "bq-3lo-auth",
        "OAUTH_CLIENT_ID": os.environ.get("OAUTH_CLIENT_ID") or file_cfg.get("OAUTH_CLIENT_ID") or "",
        "OAUTH_CLIENT_SECRET": os.environ.get("OAUTH_CLIENT_SECRET") or file_cfg.get("OAUTH_CLIENT_SECRET") or "",
    }
    if not settings["OAUTH_CLIENT_ID"]:
        for secret_file in sorted(REPO_ROOT.glob("client_secret*.json")):
            try:
                raw = json.loads(secret_file.read_text(encoding="utf-8"))
                block = raw.get("web") or raw.get("installed") or {}
                if block.get("client_id") and block.get("client_secret"):
                    settings["OAUTH_CLIENT_ID"] = block["client_id"]
                    settings["OAUTH_CLIENT_SECRET"] = block["client_secret"]
                    break
            except Exception:
                continue
    return settings


SETTINGS = _load_settings()
FQ_TABLE = f"{SETTINGS['PROJECT_ID']}.{SETTINGS['BQ_DATASET']}.{SETTINGS['BQ_TABLE']}"
_ORIG_GET_VALID_CREDENTIALS = GoogleCredentialsManager.get_valid_credentials


def _extract_token(val: object) -> Optional[str]:
    if isinstance(val, str) and val.strip():
        return val.strip()
    if isinstance(val, dict):
        tok = val.get("access_token") or (val.get("oauth2") or {}).get("access_token")
        return tok.strip() if isinstance(tok, str) and tok.strip() else None
    return None


async def _hybrid_3lo_get_valid_credentials(
    self: GoogleCredentialsManager, tool_context: ToolContext
) -> Optional[google.auth.credentials.Credentials]:
    """Resolves user OAuth credentials across Gemini Enterprise 3LO, Custom Frontends, local dev ADC, and ADK Web."""
    auth_id = SETTINGS["GEMINI_AUTH_ID"]
    state_dict = (
        tool_context.state.to_dict()
        if hasattr(tool_context.state, "to_dict")
        else dict(tool_context.state)
    )

    # 1. Gemini Enterprise 3LO & Custom Frontend Token Injection:
    #    - Gemini Enterprise injects the user's OAuth token under AUTH_ID (or .../authorizations/AUTH_ID).
    #    - Custom frontends can pass the user's 3LO token in session state under AUTH_ID or 'custom_access_token'.
    candidate_keys = (
        auth_id,
        f"temp:{auth_id}",
        "custom_access_token",
        "user_access_token",
        "bq_access_token",
    )
    for key in candidate_keys:
        if token := _extract_token(tool_context.state.get(key)):
            return google.oauth2.credentials.Credentials(token=token)
    for key, val in state_dict.items():
        if key.endswith(f"/authorizations/{auth_id}") and (token := _extract_token(val)):
            return google.oauth2.credentials.Credentials(token=token)

    # 2. Local ADC Fallback: only when explicitly requested via BQ_FORCE_LOCAL_ADC=true or no OAuth client is set
    if os.environ.get("BQ_FORCE_LOCAL_ADC", "").lower() == "true" or not SETTINGS["OAUTH_CLIENT_ID"]:
        return google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])[0]

    # 3. ADK Web / Custom Frontend Interactive OAuth: delegate to ADK's built-in 3LO OAuth consent flow
    return await _ORIG_GET_VALID_CREDENTIALS(self, tool_context)


GoogleCredentialsManager.get_valid_credentials = _hybrid_3lo_get_valid_credentials

bigquery_toolset = BigQueryToolset(
    credentials_config=BigQueryCredentialsConfig(
        client_id=SETTINGS["OAUTH_CLIENT_ID"] or "CONFIGURE_OAUTH_CLIENT_ID",
        client_secret=SETTINGS["OAUTH_CLIENT_SECRET"] or "CONFIGURE_OAUTH_CLIENT_SECRET",
        scopes=["https://www.googleapis.com/auth/bigquery"],
    ),
    bigquery_tool_config=BigQueryToolConfig(
        write_mode=WriteMode.BLOCKED,
        compute_project_id=SETTINGS["PROJECT_ID"],
        location=SETTINGS["BQ_LOCATION"],
        application_name="bq-3lo-retail-sales-agent",
        job_labels={"app": "bq-3lo-agent"},
    ),
    tool_filter=["execute_sql", "get_table_info", "list_dataset_ids", "list_table_ids"],
)

AGENT_INSTRUCTION = f"""You are the OmniRetail Regional Sales Analyst AI Agent.
You analyze weekly retail store sales in BigQuery while enforcing per-user Row-Level Security (RLS) via 3-Legged OAuth (3LO).

### Primary Table: `{FQ_TABLE}`
Columns:
- `record_id` (STRING), `week_start_date` (DATE: `2026-09-28`..`2026-10-26`), `region` (STRING: `North`, `South`, `West` — RLS-protected)
- `store_id` (STRING), `store_name` (STRING), `city` (STRING), `state` (STRING), `store_manager` (STRING)
- `top_category` (STRING), `active_promo` (STRING), `foot_traffic` (INT64), `units_sold` (INT64)
- `gross_revenue_usd` (FLOAT64), `discount_usd` (FLOAT64), `net_revenue_usd` (FLOAT64), `weekly_target_usd` (FLOAT64)
- `target_attainment_pct` (FLOAT64), `gross_margin_pct` (FLOAT64), `return_rate_pct` (FLOAT64), `stockout_incidents` (INT64)
- `manager_weekly_note` (STRING): Store manager commentary explaining weekly root causes

### Operating Rules
1. **Always Include `SESSION_USER()`**: Query `` `{FQ_TABLE}` `` via `execute_sql` and include `SESSION_USER() AS authenticated_user` so you always report which Google user BigQuery authenticated via 3LO.
2. **Synthesize KPIs + `manager_weekly_note`**: Combine numeric metrics with `manager_weekly_note` to explain *why* stores beat or missed targets (e.g., promotions, product quality returns, or supply-chain stockouts).
3. **Explain 0-Row RLS Filtering**: Never fabricate data for missing regions. If a region returns 0 rows, explain that BigQuery Row-Level Security filtered unauthorized rows for `SESSION_USER()` at the storage layer.
"""

os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", SETTINGS["PROJECT_ID"])


class GlobalGemini(Gemini):
    """Routes Gemini model calls to the 'global' Vertex AI endpoint even inside regional Agent Runtime containers."""

    @property
    def api_client(self):
        os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
        return super().api_client


root_agent = Agent(
    name="root_agent",
    model=GlobalGemini(
        model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=AGENT_INSTRUCTION,
    tools=[bigquery_toolset],
)

app = App(root_agent=root_agent, name="app")
