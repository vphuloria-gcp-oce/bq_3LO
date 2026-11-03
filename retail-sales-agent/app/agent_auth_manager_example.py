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

"""
Reference Implementation: Google Cloud IAM Auth Manager (v2 API) + SPIFFE Agent Identity.

This module demonstrates Pattern B from the official Google Cloud IAM Auth Manager
documentation and Codelab:
  - https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2
  - https://docs.cloud.google.com/iam/docs/auth-with-2lo-v2
  - https://codelabs.developers.google.com/agent-identity-auth-manager
  - https://github.com/google/adk-python/tree/main/contributing/samples/integrations/gcp_auth

When to use this pattern vs. `app/agent.py` (Pattern A):
  - Use `app/agent.py` (Pattern A: `BigQueryToolset` + `BigQueryCredentialsConfig`)
    when your primary UI is **Gemini Enterprise** or local **`adk web`**.
  - Use this module (Pattern B: `GcpAuthProvider` + `GcpAuthProviderScheme`) when
    building a **custom web/mobile frontend** backed by **Google Cloud IAM Auth Manager**
    (`agentidentity.googleapis.com/v1`) and connecting to either:
      1. Google's managed **BigQuery MCP Server** (`https://bigquery.googleapis.com/mcp`), OR
      2. Custom Python tools wrapped in **`AuthenticatedFunctionTool`**.
"""

from __future__ import annotations

import os
from typing import Any

import google.auth
import google.oauth2.credentials
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.auth.auth_credential import AuthCredential
from google.adk.auth.auth_tool import AuthConfig
from google.adk.auth.credential_manager import CredentialManager
from google.adk.models import Gemini
from google.adk.tools.authenticated_function_tool import AuthenticatedFunctionTool
from google.cloud import bigquery
from google.genai import types

# Optional imports: require `pip install "google-adk[agent-identity]"` and `pip install mcp`
try:
    from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme
except ImportError:  # pragma: no cover
    GcpAuthProvider = None  # type: ignore[assignment,misc]
    GcpAuthProviderScheme = None  # type: ignore[assignment,misc]

try:
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
except ImportError:  # pragma: no cover
    StreamableHTTPConnectionParams = None  # type: ignore[assignment,misc]
    McpToolset = None  # type: ignore[assignment,misc]


PROJECT_ID = os.environ.get("BQ_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "your-gcp-project-id"
LOCATION = os.environ.get("AUTH_PROVIDER_LOCATION", "us-central1")
BQ_DATASET = os.environ.get("BQ_DATASET", "retail_sales")
BQ_TABLE = os.environ.get("BQ_TABLE", "weekly_store_sales")

# 1. Regional IAM Auth Manager Resource Names (created via `gcloud agent-identity auth-providers create`)
AUTH_PROVIDER_3LO_NAME = os.environ.get(
    "AUTH_PROVIDER_3LO_NAME",
    f"projects/{PROJECT_ID}/locations/{LOCATION}/authProviders/bigquery-3lo-authprovider",
)
AUTH_PROVIDER_2LO_NAME = os.environ.get(
    "AUTH_PROVIDER_2LO_NAME",
    f"projects/{PROJECT_ID}/locations/{LOCATION}/authProviders/corp-api-2lo-authprovider",
)

# 2. Client-side callback endpoint that reads `consent_nonce` and calls `credentials:finalize`
CONTINUE_URI = os.environ.get("AUTH_MANAGER_CONTINUE_URI", "http://127.0.0.1:8501/validateUserId")
BIGQUERY_MCP_ENDPOINT = os.environ.get("BIGQUERY_MCP_ENDPOINT", "https://bigquery.googleapis.com/mcp")


# ==============================================================================
# Pattern B1: 3LO with Managed BigQuery MCP Server (`McpToolset`)
# Reference: https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2#connecting-bigquery-mcp
# ==============================================================================
def build_bigquery_mcp_3lo_toolset() -> McpToolset:
    """Connects to Google's managed BigQuery MCP Server using IAM Auth Manager 3LO."""
    if GcpAuthProvider is None or GcpAuthProviderScheme is None:
        raise RuntimeError("Install google-adk[agent-identity] to use GcpAuthProvider.")

    CredentialManager.register_auth_provider(GcpAuthProvider())

    bigquery_mcp_auth_scheme = GcpAuthProviderScheme(
        name=AUTH_PROVIDER_3LO_NAME,
        scopes=["https://www.googleapis.com/auth/bigquery"],
        continue_uri=CONTINUE_URI,
    )
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=BIGQUERY_MCP_ENDPOINT),
        auth_scheme=bigquery_mcp_auth_scheme,
        errlog=None,
    )


# ==============================================================================
# Pattern B2: 3LO with Custom Python `AuthenticatedFunctionTool`
# Reference: https://codelabs.developers.google.com/agent-identity-auth-manager
# ==============================================================================
async def query_bigquery_with_3lo_token(
    credential: AuthCredential, sql_query: str
) -> dict[str, Any]:
    """Executes a read-only BigQuery SQL query using the user's 3LO token from Auth Manager."""
    token = None
    if credential and credential.http and credential.http.credentials:
        token = credential.http.credentials.token

    if not token:
        return {"status": "error", "error_message": "No 3LO OAuth access token available."}

    # CRITICAL: Never cache 3LO user credentials in a global singleton.
    # Instantiate a per-request client bound to this user's short-lived Bearer token.
    user_creds = google.oauth2.credentials.Credentials(token=token)
    bq_client = bigquery.Client(project=PROJECT_ID, credentials=user_creds)
    rows = [dict(row.items()) for row in bq_client.query(sql_query).result()]
    return {"status": "success", "row_count": len(rows), "rows": rows}


def build_authenticated_function_3lo_tool() -> AuthenticatedFunctionTool:
    """Wraps a Python function in ADK's AuthenticatedFunctionTool backed by IAM Auth Manager 3LO."""
    if GcpAuthProvider is None or GcpAuthProviderScheme is None:
        raise RuntimeError("Install google-adk[agent-identity] to use GcpAuthProvider.")

    CredentialManager.register_auth_provider(GcpAuthProvider())

    bq_3lo_auth_config = AuthConfig(
        auth_scheme=GcpAuthProviderScheme(
            name=AUTH_PROVIDER_3LO_NAME,
            scopes=["https://www.googleapis.com/auth/bigquery"],
            continue_uri=CONTINUE_URI,
        )
    )
    return AuthenticatedFunctionTool(
        func=query_bigquery_with_3lo_token,
        auth_config=bq_3lo_auth_config,
    )


# ==============================================================================
# Pattern B3: 2-Legged OAuth (2LO) Machine-to-Machine with IAM Auth Manager
# Reference: https://docs.cloud.google.com/iam/docs/auth-with-2lo-v2
# ==============================================================================
def build_2lo_auth_scheme(scopes: list[str] | None = None) -> Any:
    """
    Constructs a 2LO (OAuth 2.0 Client Credentials) scheme via IAM Auth Manager.
    Notice: No `continue_uri` is needed for 2LO because no human redirect occurs.
    """
    if GcpAuthProvider is None or GcpAuthProviderScheme is None:
        raise RuntimeError("Install google-adk[agent-identity] to use GcpAuthProvider.")

    CredentialManager.register_auth_provider(GcpAuthProvider())

    return GcpAuthProviderScheme(
        name=AUTH_PROVIDER_2LO_NAME,
        scopes=scopes or ["https://www.googleapis.com/auth/bigquery"],
    )


def create_auth_manager_demo_app(use_mcp: bool = True) -> App:
    """Creates an ADK App wired with IAM Auth Manager 3LO (MCP or Function Tool)."""
    tool = build_bigquery_mcp_3lo_toolset() if use_mcp else build_authenticated_function_3lo_tool()
    agent = Agent(
        name="bq_auth_manager_agent",
        model=Gemini(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        instruction=(
            f"You are a BigQuery analyst agent using IAM Auth Manager 3LO. "
            f"Always include `SESSION_USER() AS authenticated_user` when querying "
            f"`{PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}`."
        ),
        tools=[tool],
    )
    return App(root_agent=agent, name="bq_auth_manager_app")
