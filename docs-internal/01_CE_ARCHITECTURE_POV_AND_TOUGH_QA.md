# 🧭 Internal CE Architecture POV: Scaling 3LO Across GCP, Non-ADK Frameworks & 3rd-Party SaaS

> **🔒 INTERNAL GOOGLE CLOUD CUSTOMER ENGINEERING (CE) PLAYBOOK**
> **Navigation**: **01: Architecture POV & Tough Q&A** · [02: Google Auth Platform, Branding & Audience Appendix](02_GOOGLE_AUTH_PLATFORM_APPENDIX.md) · [03: Internal Demo Playbook, Antigravity Builder & Rollback Runbook](03_INTERNAL_DEMO_AND_BUILDER_RUNBOOK.md)

---

This guide equips **Google Cloud Customer Engineers (CEs) and AI/Data Specialists** with the authoritative architectural point of view (POV), implementation blueprints, and ready-to-use code patterns for customer deep-dives on **3-Legged OAuth (3LO)** and **per-user data governance** across:
1. **The Two Google Cloud 3LO Mechanisms** (reconciling the 9 customer-shared documentation links against our repository's dual-pattern implementation).
2. **Scaling 3LO Across First-Party GCP Services** (Cloud SQL, AlloyDB, Spanner, GCS, Google Drive/Workspace, Looker, Vertex AI Search, Dataplex, Apigee API Hub, and Application Integration).
3. **Implementing 3LO in Non-ADK Frameworks** (LangGraph, LangChain, CrewAI, and custom FastAPI/Python agents via both IAM Auth Manager REST APIs and Gateway/Frontend Token Passthrough).
4. **Extending 3LO to 3rd-Party SaaS & Data Platforms** (Snowflake, Atlassian Jira & Confluence, Salesforce, ServiceNow, and GitHub via IAM Auth Manager v2).
5. **Tough Customer Q&A Cheat Sheet** (10 high-probability CISO, Data Architect, and Platform Engineering questions with crisp, defensible answers).

---

## 1. Reconciling the Customer's Shared Links vs. Our Demo Repo (The CE Mental Map)

Customers researching 3-Legged OAuth for AI agents on Google Cloud frequently come across **9 official documentation, Codelab, and GitHub links** that appear to describe two completely different ways of wiring 3LO in ADK:

| # | Customer-Shared Reference Link | Which 3LO Mechanism Does It Use? |
| :--- | :--- | :--- |
| **1** | [Use 3LO to authenticate end users (`docs.cloud.google.com/iam/docs/auth-with-3lo-v2`)](https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2) | **Mechanism B**: IAM Auth Manager v2 (`GcpAuthProvider` + `GcpAuthProviderScheme`) |
| **2** | [Use 2LO to authenticate workloads (`docs.cloud.google.com/iam/docs/auth-with-2lo-v2`)](https://docs.cloud.google.com/iam/docs/auth-with-2lo-v2) | **Mechanism B (2LO variant)**: IAM Auth Manager v2 (`GcpAuthProviderScheme` without `continue_uri`) |
| **3** | [Codelab: Agent Identity & Auth Manager (`codelabs.developers.google.com/agent-identity-auth-manager`)](https://codelabs.developers.google.com/agent-identity-auth-manager) | **Mechanism B**: SPIFFE Agent Identity + IAM Auth Manager v2 + `AuthenticatedFunctionTool` + Custom Streamlit `/validateUserId` |
| **4** | [ADK Sample: `gcp_auth` (`github.com/google/adk-python/.../samples/integrations/gcp_auth`)](https://github.com/google/adk-python/tree/main/contributing/samples/integrations/gcp_auth) | **Mechanism B**: `GcpAuthProvider` + BigQuery MCP (`https://bigquery.googleapis.com/mcp`) |
| **5** | [ADK Docs: Authentication (`google.github.io/adk-docs/tools-custom/authentication`)](https://google.github.io/adk-docs/tools-custom/authentication/) | **Mechanism A**: Standard ADK OAuth2 (`AuthCredential`, `OAuth2Auth`, `GoogleCredentialsManager`) |
| **6** | [ADK Docs: BigQuery Toolset (`google.github.io/adk-docs/integrations/bigquery`)](https://google.github.io/adk-docs/integrations/bigquery/) | **Mechanism A**: `BigQueryToolset` + `BigQueryCredentialsConfig` |
| **7** | [Gemini Enterprise: Register & Authorize ADK Agents](https://cloud.google.com/generative-ai-app-builder/docs/register-adk-agent) | **Mechanism A**: Discovery Engine `Authorization` (`serverSideOauth2`) + Session State Token Injection |
| **8** | [Vertex AI Agent Engine: Agent Identity (`cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/agent-identity`)](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/agent-identity) | **Plane 1 Workload Identity**: SPIFFE `AGENT_IDENTITY` (`principal://agents.global.org-...`) — works with **both** Mechanism A and Mechanism B! |
| **9** | [BigQuery Row-Level Security (`cloud.google.com/bigquery/docs/row-level-security-intro`)](https://cloud.google.com/bigquery/docs/row-level-security-intro) | **Data Plane Enforcement**: Evaluates `SESSION_USER()` from the 3LO Bearer token — identical across **both** mechanisms! |

### Why Two Distinct 3LO Mechanisms Exist—And How This Repo Includes Both

```mermaid
flowchart TB
    subgraph MechA["Mechanism A: Native ADK BigQueryToolset + Gemini Enterprise Authorization (app/agent.py)"]
        direction LR
        UI_A["Gemini Enterprise UI OR Local adk web"] -->|"GE injects state['bq-3lo-auth'] OR adk web runs OAuth"| CredMgr["GoogleCredentialsManager + _hybrid_3lo_get_valid_credentials"]
        CredMgr -->|"google.oauth2.credentials.Credentials(token)"| BQToolset["First-Party BigQueryToolset (execute_sql)"]
    end

    subgraph MechB["Mechanism B: IAM Auth Manager v2 + GcpAuthProvider (app/agent_auth_manager_example.py)"]
        direction LR
        UI_B["Custom Web/Mobile App (with /validateUserId route)"] -->|"2-Phase Commit: retrieveCredentials + finalize"| AuthMgr["IAM Auth Manager (agentidentitycredentials.googleapis.com)"]
        AuthMgr -->|"GcpAuthProviderScheme injects AuthCredential"| MCP_Or_Fn["McpToolset (bigquery.googleapis.com/mcp) OR AuthenticatedFunctionTool"]
    end

    BQToolset & MCP_Or_Fn ==>|"Identical HTTP Header: Authorization: Bearer ya29.<user_token>"| BQRls[("BigQuery Storage Engine + Row-Level Security (SESSION_USER())")]
```

#### Mechanism A (`BigQueryToolset` + `BigQueryCredentialsConfig` + Gemini Enterprise `DiscoveryEngine Authorization`) — Implemented in [`retail-sales-agent/app/agent.py`](../retail-sales-agent/app/agent.py)
- **Why `BigQueryToolset` Uses `GoogleCredentialsManager` Instead of `GcpAuthProviderScheme`**:
  - ADK's built-in first-party `BigQueryToolset` (`google.adk.integrations.bigquery`) was architected around `BigQueryCredentialsConfig` and `GoogleCredentialsManager` (`google/adk/tools/_google_credentials.py`). It does **not** accept an `AuthScheme` or `GcpAuthProviderScheme` parameter in its constructor.
- **Why Standard ADK Makes `external_access_token_key` and `client_id`/`client_secret` Mutually Exclusive**:
  - In upstream `google-adk`, `BigQueryCredentialsConfig` validates that you pass *either* `external_access_token_key="bq-3lo-auth"` (where an external host like Gemini Enterprise injects a raw bearer token into `tool_context.state`) *or* `client_id` + `client_secret` (where `adk web` runs an interactive OAuth flow), raising a `ValidationError` if you pass both.
- **How `_hybrid_3lo_get_valid_credentials` in [`app/agent.py`](../retail-sales-agent/app/agent.py) Bridges All Three Surfaces**:
  - Our repo configures `BigQueryCredentialsConfig(client_id=..., client_secret=..., scopes=[...])` so `adk web` works natively out of the box, and wraps `GoogleCredentialsManager.get_valid_credentials` with `_hybrid_3lo_get_valid_credentials`.
  - On every tool call, `_hybrid_3lo_get_valid_credentials` **first** inspects `tool_context.state` for a pre-injected token from **Gemini Enterprise** (`state["bq-3lo-auth"]`, `state["temp:bq-3lo-auth"]`, or `.../authorizations/bq-3lo-auth`) or a **Custom Frontend** (`state["custom_access_token"]`). If found, it immediately returns `google.oauth2.credentials.Credentials(token=token)` without triggering a duplicate OAuth prompt.
  - If no pre-injected token exists in `tool_context.state`, it delegates to `_ORIG_GET_VALID_CREDENTIALS`, which renders the interactive **Authorize with Google** button inside local `adk web`!

#### Mechanism B (`GcpAuthProvider` + `GcpAuthProviderScheme` + IAM Auth Manager v2 `agentidentity.googleapis.com`) — Implemented in [`retail-sales-agent/app/agent_auth_manager_example.py`](../retail-sales-agent/app/agent_auth_manager_example.py)
- **What It Pairs With**:
  - `GcpAuthProvider` (`from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme`) registers into `CredentialManager` and pairs with tools that accept an `AuthScheme` / `AuthConfig`—specifically:
    1. **`McpToolset`** (e.g., connecting to Google's managed BigQuery MCP server at `https://bigquery.googleapis.com/mcp` or a 3rd-party MCP server).
    2. **`AuthenticatedFunctionTool`** (wrapping custom Python functions that receive an `AuthCredential` object).
    3. **`OpenAPIToolset` / `APIHubToolset`**.
- **Why Standard `adk web` Does NOT Support 3LO with `GcpAuthProvider`**:
  - As explicitly documented in the official IAM Auth Manager Codelab and docs, **IAM Auth Manager 3LO uses a 2-phase commit protocol** designed to prevent session fixation attacks:
    1. When `GcpAuthProvider` calls `authProviders:retrieveCredentials`, Auth Manager returns `"uri_consent_required"` containing an `authorization_uri` **and a cryptographic `consent_nonce`**.
    2. The user authorizes at the IdP, which redirects to `https://agentidentitycredentials.googleapis.com/v1/projects/.../authProviders/.../oauthcallback`.
    3. Auth Manager then redirects the user's browser to your custom `continue_uri` (e.g., `http://127.0.0.1:8501/validateUserId`).
    4. **Crucial Step**: That `/validateUserId` endpoint must authenticate the logged-in frontend user and call `POST https://agentidentitycredentials.googleapis.com/v1/.../credentials:finalize` passing the `consent_nonce` to bind the OAuth token to that session/user ID!
  - Because ADK's built-in `adk web` developer UI (`http://localhost:8000/dev-ui/`) only implements standard 1-phase OAuth redirect handling and does **not** host a `/validateUserId` route that calls `credentials:finalize` with a `consent_nonce`, `GcpAuthProvider` 3LO requires a custom web frontend (like the Streamlit frontend in the Codelab) rather than `adk web`.

### Summary Decision Matrix for CEs

| Customer Architecture Scenario | Recommended 3LO Mechanism | File to Show in This Repo |
| :--- | :--- | :--- |
| **Gemini Enterprise UI** + ADK `BigQueryToolset` (or local **`adk web`** demo) | **Mechanism A** (`BigQueryCredentialsConfig` + `DiscoveryEngine Authorization` + Hybrid Resolver) | [`retail-sales-agent/app/agent.py`](../retail-sales-agent/app/agent.py) |
| **Custom Web/Mobile App** + Managed **BigQuery MCP Server** (`https://bigquery.googleapis.com/mcp`) or **3rd-Party SaaS** (Snowflake, Jira, Salesforce) | **Mechanism B** (IAM Auth Manager v2 `GcpAuthProvider` + `GcpAuthProviderScheme` + `/validateUserId`) | [`retail-sales-agent/app/agent_auth_manager_example.py`](../retail-sales-agent/app/agent_auth_manager_example.py) |

---

## 2. Scaling 3LO Tomorrow to Other Google Cloud Services (Beyond BigQuery)

When a customer says, *"This BigQuery 3LO + RLS pattern is exactly what our CISO wants—how do we extend it tomorrow to Cloud SQL, AlloyDB, Spanner, GCS, Google Drive, Looker, Vertex AI Search, Dataplex, and Apigee?"*, use this service-by-service architectural guide and code reference.

### 2.1 Cloud SQL (PostgreSQL / MySQL), AlloyDB & Cloud Spanner

Relational and distributed databases on GCP support two distinct 3LO enforcement patterns:

#### Pattern 1: Native IAM Database Authentication + PostgreSQL RLS (Cloud SQL & AlloyDB)
1. **Database Setup**:
   - Enable `cloudsql.iam_authentication = on` (or `alloydb.iam_authentication = on`).
   - Grant the human user `roles/cloudsql.instanceUser` (or `roles/alloydb.databaseUser` + `roles/alloydb.client`).
   - Create the IAM user inside PostgreSQL and attach native PostgreSQL Row-Level Security:
     ```sql
     CREATE USER "uno@vphuloria.altostrat.com" WITH LOGIN;
     GRANT SELECT ON TABLE regional_orders TO "uno@vphuloria.altostrat.com";
     ALTER TABLE regional_orders ENABLE ROW LEVEL SECURITY;

     CREATE POLICY north_only_policy ON regional_orders
       FOR SELECT
       TO "uno@vphuloria.altostrat.com"
       USING (region = 'North');
     ```
2. **OAuth Scope**: Request `https://www.googleapis.com/auth/sqlservice.login` (Cloud SQL) or `https://www.googleapis.com/auth/cloud-platform` (AlloyDB).
3. **Agent Tool Code**: Pass the user's 3LO access token directly as the PostgreSQL `password` when opening a per-request connection (PostgreSQL `current_user` / `session_user` evaluates to `uno@vphuloria.altostrat.com`!):
   ```python
   import sqlalchemy
   from google.adk.tools.tool_context import ToolContext

   def query_cloudsql_with_3lo(sql_query: str, tool_context: ToolContext) -> list[dict]:
       """Executes a read-only PostgreSQL query as the 3LO human user via IAM DB Auth."""
       user_token = tool_context.state.get("bq-3lo-auth")
       user_email = tool_context.state.get("user_email")  # e.g., "uno@vphuloria.altostrat.com"
       if not user_token or not user_email:
           raise PermissionError("Missing 3LO OAuth token or user email.")

       # In IAM DB Auth, the short-lived 3LO OAuth access token IS the database password
       engine = sqlalchemy.create_engine(
           sqlalchemy.engine.url.URL.create(
               drivername="postgresql+pg8000",
               username=user_email,
               password=user_token,
               host="10.128.0.5",  # Private IP or Cloud SQL Python Connector
               port=5432,
               database="retail_db",
           ),
           pool_pre_ping=True,
       )
       with engine.connect() as conn:
           conn.exec_driver_sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
           result = conn.exec_driver_sql(sql_query)
           return [dict(row._mapping) for row in result]
   ```

#### Pattern 2: Cloud Spanner Fine-Grained Access Control (FGAC)
1. **Database Setup**:
   - Create Spanner Database Roles (`CREATE ROLE north_analyst; GRANT SELECT ON TABLE store_sales TO ROLE north_analyst;`) or parameterized secure views.
   - Bind the user to `roles/spanner.fineGrainedAccessUser` with an IAM Condition restricting `resource.name.endsWith('/databaseRoles/north_analyst')`.
2. **OAuth Scope**: `https://www.googleapis.com/auth/spanner.data`.
3. **Agent Tool Code**:
   ```python
   import google.oauth2.credentials
   from google.cloud import spanner

   def query_spanner_3lo(sql_query: str, db_role: str, user_token: str) -> list[dict]:
       creds = google.oauth2.credentials.Credentials(token=user_token)
       client = spanner.Client(project="bq-3lo", credentials=creds)
       instance = client.instance("retail-spanner")
       # Spanner enforces IAM FGAC verifying that user_token is allowed to assume `db_role`
       database = instance.database("sales-db", database_role=db_role)
       with database.snapshot() as snapshot:
           results = snapshot.execute_sql(sql_query)
           return [dict(zip([col.name for col in results.fields], row)) for row in results]
   ```

---

### 2.2 Google Cloud Storage (GCS) & Google Drive / Workspace APIs

Both GCS and Google Workspace APIs (Drive, Gmail, Calendar, Docs, Sheets) are **100% OAuth 2.0 Bearer-token native**.

- **OAuth Scopes**:
  - **GCS**: `https://www.googleapis.com/auth/devstorage.read_only` (enforces Bucket IAM, **GCS Managed Folders**, and **IAM Conditions** on object prefixes like `resource.name.startsWith("projects/_/buckets/omni-reports/objects/north/")`).
  - **Google Drive**: `https://www.googleapis.com/auth/drive.readonly` (enforces exact Drive file/folder sharing ACLs for the human user).
- **Code Pattern A — Per-Request Python Tool (GCS & Drive)**:
  ```python
  import google.oauth2.credentials
  from google.cloud import storage
  from googleapiclient.discovery import build

  def read_user_gcs_report(bucket_name: str, blob_path: str, user_token: str) -> str:
      """Reads a GCS object using the end-user's 3LO token (enforces Managed Folder IAM)."""
      user_creds = google.oauth2.credentials.Credentials(token=user_token)
      storage_client = storage.Client(project="bq-3lo", credentials=user_creds)
      blob = storage_client.bucket(bucket_name).blob(blob_path)
      return blob.download_as_text()

  def search_user_google_drive(query: str, user_token: str) -> list[dict]:
      """Searches Google Drive returning ONLY files shared with the signed-in human user."""
      user_creds = google.oauth2.credentials.Credentials(token=user_token)
      drive_service = build("drive", "v3", credentials=user_creds)
      resp = drive_service.files().list(
          q=f"name contains '{query}' and trashed = false",
          pageSize=10,
          fields="files(id, name, mimeType, owners)",
      ).execute()
      return resp.get("files", [])
  ```
- **Code Pattern B — Declarative `GoogleApiToolset` / `OpenAPIToolset`**:
  ADK also provides `GoogleApiToolset` (`from google.adk.tools.google_api_tool import GoogleApiToolset`), which auto-generates tools from Google Discovery Documents (e.g., `drive:v3`, `gmail:v1`, `calendar:v3`) and wires directly into `OAuth2Auth` or `GcpAuthProviderScheme`.

---

### 2.3 Looker, Vertex AI Search & Dataplex Catalog

| Service | OAuth Scope | How 3LO User Permissions Are Enforced |
| :--- | :--- | :--- |
| **Looker & Conversational Analytics API (`geminidataanalytics`)** | `https://www.googleapis.com/auth/bigquery` + `https://www.googleapis.com/auth/cloud-platform` | Pass the user's 3LO `Credentials(token=user_token)` to the Conversational Analytics client or Looker MCP server. Looker's semantic layer (`access_filter` in LookML) and BigQuery RLS both evaluate the human user's identity. |
| **Vertex AI Search (Discovery Engine Data Stores)** | Native Workspace login in Gemini Enterprise (`idpType: GSUITE`) or `https://www.googleapis.com/auth/cloud-platform` | When a Data Store with **Document-Level ACLs** (`acl_enabled=True`) is queried, Discovery Engine intersects the vector/keyword search results with the signed-in user's email and Google Groups (`aclInfo.readers`) inside the search index. |
| **Dataplex Universal Catalog** | `https://www.googleapis.com/auth/cloud-platform.read-only` | Calling `dataplex_v1.CatalogServiceClient(credentials=Credentials(token=user_token)).search_entries(...)` returns **only** metadata entries, glossary terms, and tables where the human user holds `dataplex.entryView` / `bigquery.tables.get`. |

---

### 2.4 Apigee API Hub & Application Integration (`APIHubToolset` & `ApplicationIntegrationToolset`)

Enterprise customers exposing internal microservices through **Apigee API Hub** or orchestrating ERP/CRM workflows via **Google Cloud Application Integration** can attach 3LO directly to ADK's built-in toolsets:

```python
from google.adk.tools.apihub_tool.apihub_toolset import APIHubToolset
from google.adk.tools.application_integration_tool.application_integration_toolset import (
    ApplicationIntegrationToolset,
)
from google.adk.integrations.agent_identity import GcpAuthProviderScheme

# 1. Apigee API Hub Toolset with per-user 3LO token forwarding
apigee_3lo_scheme = GcpAuthProviderScheme(
    name="projects/bq-3lo/locations/us-central1/authProviders/apigee-3lo-provider",
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
    continue_uri="https://portal.corp.example.com/validateUserId",
)

order_management_api_tools = APIHubToolset(
    apihub_resource_name="projects/bq-3lo/locations/us-central1/apis/order-service/versions/v1/specs/openapi",
    auth_scheme=apigee_3lo_scheme,
)

# 2. Application Integration Toolset (triggers Integration workflows or Connectors)
erp_integration_tools = ApplicationIntegrationToolset(
    project="bq-3lo",
    location="us-central1",
    integration="sap-order-lookup-integration",
    triggers=["api_trigger/sap_order_lookup"],
    auth_scheme=apigee_3lo_scheme,
)
```

---

## 3. How 3LO Works in Non-ADK Frameworks (LangGraph, LangChain, CrewAI, Custom Python)

Customers running **LangGraph**, **LangChain**, **CrewAI**, or **custom FastAPI agents** (hosted on Vertex AI Agent Runtime, Cloud Run, or GKE) can implement Google Cloud 3LO using **two proven architectural approaches**.

```mermaid
sequenceDiagram
    autonumber
    actor User as "End User (Browser)"
    participant App as "Custom Frontend / BFF"
    participant Agent as "LangGraph / CrewAI Agent"
    participant AuthMgr as "IAM Auth Manager API (agentidentitycredentials.googleapis.com)"
    participant GoogleIdP as "Google / 3rd-Party OAuth Server"
    participant BQ as "BigQuery (RLS Enforced)"

    User->>App: "Show my regional store sales"
    App->>Agent: Invoke LangGraph turn (session_id="sess-99", user_id="uno@domain")
    Agent->>AuthMgr: POST .../authProviders/bq-3lo:retrieveCredentials (session_id="sess-99")
    alt First Time (No Token Cached in Auth Manager)
        AuthMgr-->>Agent: Returns uri_consent_required {authorization_uri, consent_nonce}
        Agent-->>App: Interrupts graph & returns authorization_uri + consent_nonce
        App-->>User: Redirects user to authorization_uri
        User->>GoogleIdP: Approves OAuth Consent
        GoogleIdP-->>AuthMgr: 302 Redirect to .../authProviders/bq-3lo/oauthcallback?code=...
        AuthMgr-->>App: 302 Redirect to continue_uri (/validateUserId)
        App->>AuthMgr: POST .../credentials:finalize {consent_nonce, session_id="sess-99"}
        AuthMgr-->>App: 200 OK (Token bound to session_id="sess-99")
    end
    App->>Agent: Resume LangGraph turn (session_id="sess-99")
    Agent->>AuthMgr: POST .../authProviders/bq-3lo:retrieveCredentials (session_id="sess-99")
    AuthMgr-->>Agent: Returns {access_token: "ya29..."} (auto-refreshed if expired!)
    Agent->>BQ: bigquery.Client(credentials=Credentials(token="ya29...")).query(...)
    BQ-->>Agent: Returns 15 North rows for SESSION_USER() = uno@domain
    Agent-->>App: Synthesized response
```

### Approach 3A — Direct Integration with IAM Auth Manager REST API (`agentidentitycredentials.googleapis.com/v1`)

In this architecture, your non-ADK agent delegates all OAuth client secrets, token exchanges, encrypted refresh-token storage, and automatic token refreshes to **Google Cloud IAM Auth Manager**.

#### Step-by-Step Protocol Flow
1. **Step 1 (`:retrieveCredentials`)**: The LangGraph/LangChain agent (running with SPIFFE `AGENT_IDENTITY` or a Service Account holding `roles/agentidentity.user`) calls:
   `POST https://agentidentitycredentials.googleapis.com/v1/projects/{P}/locations/{L}/authProviders/{ID}:retrieveCredentials`
   passing the user's `session_id` (or principal identifier) and requested `scopes`.
2. **Step 2 (`uri_consent_required`)**: If the user has not yet consented, the API returns a response containing `uri_consent_required` with an `authorization_uri` and a `consent_nonce`.
3. **Step 3 (OAuth Redirect & `/oauthcallback`)**: Your frontend redirects the user to `authorization_uri`. After the user consents at Google (or a 3rd-party IdP), the IdP redirects to Auth Manager's managed callback (`https://agentidentitycredentials.googleapis.com/v1/projects/{P}/locations/{L}/authProviders/{ID}/oauthcallback`), which exchanges the code for tokens and redirects the user's browser to your `continue_uri` (`/validateUserId`).
4. **Step 4 (`:finalize`)**: Your `/validateUserId` backend route verifies the user's session and calls:
   `POST https://agentidentitycredentials.googleapis.com/v1/projects/{P}/locations/{L}/authProviders/{ID}:finalizeCredentials` (or `.../credentials:finalize`) with the `consent_nonce`.
5. **Step 5 (Execute Tool with Bearer Token)**: The LangGraph tool node calls `:retrieveCredentials` again, receives the active `access_token`, and instantiates a request-scoped `bigquery.Client(credentials=Credentials(token=access_token))`.

#### Copy-Pasteable Python Snippet: LangGraph Tool Node Using IAM Auth Manager REST API

```python
import google.auth
import google.auth.transport.requests
import google.oauth2.credentials
import httpx
from google.cloud import bigquery
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt

PROJECT_ID = "bq-3lo"
LOCATION = "us-central1"
AUTH_PROVIDER_ID = "bigquery-3lo-authprovider"
CONTINUE_URI = "https://sales-portal.example.com/validateUserId"

AUTH_MANAGER_BASE_URL = (
    f"https://agentidentitycredentials.googleapis.com/v1/"
    f"projects/{PROJECT_ID}/locations/{LOCATION}/authProviders/{AUTH_PROVIDER_ID}"
)


def _get_agent_plane1_headers() -> dict[str, str]:
    """Obtains the agent workload's Plane 1 (2LO / SPIFFE Agent Identity) token to call Auth Manager."""
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
        "x-goog-user-project": PROJECT_ID,
    }


def retrieve_3lo_token_from_auth_manager(session_id: str, scopes: list[str]) -> dict:
    """Calls IAM Auth Manager :retrieveCredentials for the current user session."""
    payload = {
        "clientIdentifier": {"sessionId": session_id},
        "scopes": scopes,
        "continueUri": CONTINUE_URI,
    }
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            f"{AUTH_MANAGER_BASE_URL}:retrieveCredentials",
            headers=_get_agent_plane1_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


def finalize_3lo_consent_in_auth_manager(session_id: str, consent_nonce: str) -> None:
    """Called by your frontend's /validateUserId endpoint after the user returns from OAuth consent."""
    payload = {
        "clientIdentifier": {"sessionId": session_id},
        "consentNonce": consent_nonce,
    }
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            f"{AUTH_MANAGER_BASE_URL}/credentials:finalize",
            headers=_get_agent_plane1_headers(),
            json=payload,
        )
        resp.raise_for_status()


@tool
def langgraph_query_bigquery_3lo(sql_query: str, config: RunnableConfig) -> list[dict]:
    """Executes a read-only BigQuery SQL query in LangGraph using IAM Auth Manager 3LO."""
    session_id = config.get("configurable", {}).get("thread_id", "default-session")
    scopes = ["https://www.googleapis.com/auth/bigquery"]

    # Step 1: Request the user's 3LO token from IAM Auth Manager
    cred_response = retrieve_3lo_token_from_auth_manager(session_id=session_id, scopes=scopes)

    # Step 2: If consent is required, pause the LangGraph execution via `interrupt()`
    # and hand `authorization_uri` + `consent_nonce` back to the frontend!
    if "uriConsentRequired" in cred_response or "uri_consent_required" in cred_response:
        consent_info = cred_response.get("uriConsentRequired") or cred_response["uri_consent_required"]
        interrupt(
            {
                "action": "oauth_3lo_consent_required",
                "authorization_uri": consent_info.get("authorizationUri") or consent_info.get("authorization_uri"),
                "consent_nonce": consent_info.get("consentNonce") or consent_info.get("consent_nonce"),
                "session_id": session_id,
            }
        )

    # Step 5: Extract the active Bearer access_token and execute BigQuery query with RLS
    access_token = (
        cred_response.get("accessToken")
        or cred_response.get("access_token")
        or cred_response.get("oauth2", {}).get("accessToken")
    )
    if not access_token:
        raise PermissionError(f"Auth Manager did not return an access token: {cred_response}")

    # Enforce read-only SELECT guardrail
    if not sql_query.strip().upper().startswith(("SELECT", "WITH")):
        raise ValueError("Only read-only SELECT/WITH queries are allowed.")

    user_creds = google.oauth2.credentials.Credentials(token=access_token)
    bq_client = bigquery.Client(project=PROJECT_ID, credentials=user_creds, location="US")
    rows = bq_client.query(sql_query).result()
    return [dict(row.items()) for row in rows]
```

---

### Approach 3B — Frontend / API Gateway (BFF) OAuth Token Passthrough

When the customer's web application or Backend-for-Frontend (BFF)—or **Gemini Enterprise**—already performs the OAuth 2.0 Authorization Code + PKCE flow:
1. The Frontend/BFF exchanges the authorization code for the user's OAuth 2.0 `access_token` (and stores the `refresh_token` in its own encrypted session store or Discovery Engine `Authorization` resource).
2. On each agent request, the Frontend/BFF passes the short-lived `access_token` in the request header (`Authorization: Bearer ya29...` or `X-User-OAuth-Token: ya29...`) or in the LangGraph `RunnableConfig` (`config={"configurable": {"bq-3lo-auth": user_access_token}}`).
3. Inside the LangGraph tool node, the tool extracts `config["configurable"]["bq-3lo-auth"]`, wraps it in `google.oauth2.credentials.Credentials(token=user_access_token)`, and calls `bigquery.Client(project=PROJECT_ID, credentials=user_creds)`.

> [!TIP]
> **When to Pitch Approach 3A vs. Approach 3B**:
> - Pitch **Approach 3A (IAM Auth Manager API)** when the customer's agent connects to **multiple heterogeneous tools** (e.g., BigQuery + Snowflake + Jira + ServiceNow) and the customer wants Google Cloud to centrally vault refresh tokens and manage multi-provider OAuth callbacks.
> - Pitch **Approach 3B (BFF / Gemini Enterprise Token Passthrough)** when the agent is surfaced inside **Gemini Enterprise** or an existing enterprise web portal that already manages Google OAuth sessions.

---

## 4. How 3LO Works for 3rd-Party Services (Snowflake, Atlassian Confluence / Jira, Salesforce, ServiceNow, GitHub)

A top follow-up question from enterprise architects is:
> *"Our enterprise data and knowledge don't just live in BigQuery—we have financial tables in **Snowflake**, tickets in **Jira**, runbooks in **Confluence**, accounts in **Salesforce**, and incidents in **ServiceNow**. Can our Vertex AI / ADK agent use 3LO so each employee only queries the Snowflake rows, Jira issues, and Confluence spaces they have permission to see?"*

**Yes—100%.** Neither Gemini Enterprise `Authorizations` nor Google Cloud **IAM Auth Manager (`agentidentity.googleapis.com`)** is hardcoded to Google's OAuth server! Both act as universal **OAuth 2.0 Relying Party (RP) Credential Brokers** that can point to **any standards-compliant 3rd-party OAuth 2.0 `authorization_uri` and `token_uri`**.

### Universal 4-Step 3rd-Party 3LO Architecture with IAM Auth Manager v2

```mermaid
flowchart LR
    subgraph Step1["1. 3rd-Party Dev Portal"]
        SaaSApp["Snowflake / Atlassian / Salesforce OAuth Client"]
    end

    subgraph Step2["2. GCP IAM Auth Manager"]
        AuthProv["authProviders/<saas>-3lo (Stores Client ID/Secret + 3rd-Party Auth/Token URIs)"]
    end

    subgraph Step3["3. ADK Agent (Vertex AI)"]
        Scheme["GcpAuthProviderScheme + McpToolset / AuthenticatedFunctionTool"]
    end

    subgraph Step4["4. 3rd-Party SaaS API"]
        RBAC[("Native SaaS RBAC & Row Policies Enforced per User!")]
    end

    Step1 -->|"Register Redirect URI: .../authProviders/<id>/oauthcallback"| Step2
    Step2 -->|"Injects 3rd-Party Bearer Token"| Step3
    Step3 -->|"Authorization: Bearer <3rd_party_user_token>"| Step4
```

#### Step 1: Register the OAuth Client in the 3rd-Party Developer Portal
Create an OAuth 2.0 Client in the target 3rd-party platform and register Google Cloud IAM Auth Manager's exact callback URL as the **Authorized Redirect URI**:
```text
https://agentidentitycredentials.googleapis.com/v1/projects/<PROJECT_ID>/locations/<LOCATION>/authProviders/<PROVIDER_NAME>/oauthcallback
```
*(Or, if surfacing exclusively through **Gemini Enterprise** using a Discovery Engine `Authorization` resource, register `https://vertexaisearch.cloud.google.com/oauth-redirect`!)*

- **In Snowflake**: Run the following SQL as `ACCOUNTADMIN` to create a custom OAuth Security Integration:
  ```sql
  CREATE OR REPLACE SECURITY INTEGRATION gcp_agent_snowflake_3lo
    TYPE = OAUTH
    ENABLED = TRUE
    OAUTH_CLIENT = CUSTOM
    OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
    OAUTH_REDIRECT_URI = 'https://agentidentitycredentials.googleapis.com/v1/projects/bq-3lo/locations/us-central1/authProviders/snowflake-3lo-provider/oauthcallback'
    OAUTH_ISSUE_REFRESH_TOKENS = TRUE
    OAUTH_REFRESH_TOKEN_VALIDITY = 7776000;

  -- Retrieve the Client ID and Client Secret:
  SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('GCP_AGENT_SNOWFLAKE_3LO');
  ```
- **In Atlassian Developer Console (Jira & Confluence Cloud)**:
  Create an **OAuth 2.0 (3LO)** integration at `https://developer.atlassian.com/console/myapps/`, enable Rotating Refresh Tokens, add granular scopes (`read:jira-work`, `read:confluence-content.all`, `offline_access`), and set the Callback URL to the `.../authProviders/atlassian-3lo-provider/oauthcallback` URI above.

#### Step 2: Create the IAM Auth Manager Resource in Google Cloud

| 3rd-Party Platform | `authorization_uri` | `token_uri` | Recommended 3LO Scopes | Native 3rd-Party Security Enforced by Token |
| :--- | :--- | :--- | :--- | :--- |
| **Snowflake** | `https://<account>.snowflakecomputing.com/oauth/authorize` | `https://<account>.snowflakecomputing.com/oauth/token-request` | `session:role:ANALYST` *(or `refresh_token`)* | **Snowflake RBAC Roles**, **Row Access Policies**, & **Dynamic Data Masking** (`CURRENT_USER()`, `CURRENT_ROLE()`). |
| **Atlassian Jira & Confluence** | `https://auth.atlassian.com/authorize?audience=api.atlassian.com` | `https://auth.atlassian.com/oauth/token` | `read:jira-work` `read:confluence-content.all` `offline_access` | **Jira Project/Issue Permissions** & **Confluence Space/Page View Restrictions**. |
| **Salesforce** | `https://login.salesforce.com/services/oauth2/authorize` | `https://login.salesforce.com/services/oauth2/token` | `api` `refresh_token` `chatter_api` | **Salesforce Object/Field-Level Security (FLS)**, **Sharing Rules**, & **Role Hierarchy**. |
| **ServiceNow** | `https://<instance>.service-now.com/oauth_auth.do` | `https://<instance>.service-now.com/oauth_token.do` | `useraccount` | **ServiceNow Contextual ACLs** (`gs.getUserID()`) on Incidents, CMDB, and Knowledge Base articles. |
| **GitHub Enterprise / Cloud** | `https://github.com/login/oauth/authorize` | `https://github.com/login/oauth/access_token` | `repo` `read:org` `read:user` | **GitHub Repository Read/Write Permissions** & **Organization Team Visibility**. |

```bash
# Example: Registering Snowflake 3LO in GCP IAM Auth Manager
gcloud alpha agent-identity auth-providers create snowflake-3lo-provider \
  --project="bq-3lo" \
  --location="us-central1" \
  --oauth2-client-id="${SNOWFLAKE_OAUTH_CLIENT_ID}" \
  --oauth2-client-secret="${SNOWFLAKE_OAUTH_CLIENT_SECRET}" \
  --authorization-uri="https://xy12345.us-central1.gcp.snowflakecomputing.com/oauth/authorize" \
  --token-uri="https://xy12345.us-central1.gcp.snowflakecomputing.com/oauth/token-request"

# Example: Registering Atlassian Jira/Confluence 3LO in GCP IAM Auth Manager
gcloud alpha agent-identity auth-providers create atlassian-3lo-provider \
  --project="bq-3lo" \
  --location="us-central1" \
  --oauth2-client-id="${ATLASSIAN_CLIENT_ID}" \
  --oauth2-client-secret="${ATLASSIAN_CLIENT_SECRET}" \
  --authorization-uri="https://auth.atlassian.com/authorize?audience=api.atlassian.com&prompt=consent" \
  --token-uri="https://auth.atlassian.com/oauth/token"
```

#### Step 3: Attach `GcpAuthProviderScheme` to ADK Tools (`McpToolset`, `AuthenticatedFunctionTool`, or `OpenAPIToolset`)

```python
import httpx
import snowflake.connector
from google.adk.auth.auth_credential import AuthCredential
from google.adk.auth.auth_tool import AuthConfig
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme
from google.adk.tools.authenticated_function_tool import AuthenticatedFunctionTool
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

CredentialManager.register_auth_provider(GcpAuthProvider())

# --- 3A. Snowflake 3LO Function Tool (enforces Snowflake Row Access Policies for CURRENT_USER()) ---
async def query_snowflake_3lo(credential: AuthCredential, sql_query: str) -> list[dict]:
    """Executes a read-only Snowflake query using the end-user's 3LO Snowflake OAuth token."""
    sf_token = credential.http.credentials.token
    conn = snowflake.connector.connect(
        account="xy12345.us-central1.gcp",
        authenticator="oauth",
        token=sf_token,
        warehouse="COMPUTE_WH",
        database="RETAIL_DW",
        schema="SALES",
    )
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(sql_query)
        return cur.fetchall()
    finally:
        conn.close()

snowflake_3lo_tool = AuthenticatedFunctionTool(
    func=query_snowflake_3lo,
    auth_config=AuthConfig(
        auth_scheme=GcpAuthProviderScheme(
            name="projects/bq-3lo/locations/us-central1/authProviders/snowflake-3lo-provider",
            scopes=["session:role:ANALYST"],
            continue_uri="https://sales-portal.example.com/validateUserId",
        )
    ),
)

# --- 3B. Atlassian Jira / Confluence 3LO via Remote MCP Server (`McpToolset`) ---
atlassian_3lo_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url="https://mcp.atlassian.com/v1/sse"),
    auth_scheme=GcpAuthProviderScheme(
        name="projects/bq-3lo/locations/us-central1/authProviders/atlassian-3lo-provider",
        scopes=["read:jira-work", "read:confluence-content.all", "offline_access"],
        continue_uri="https://sales-portal.example.com/validateUserId",
    ),
)
```

#### Step 4: Runtime RBAC & Row Policy Enforcement
At runtime, when `uno@vphuloria.altostrat.com` asks a cross-platform question (*"Compare my North region sales in BigQuery and Snowflake and check open supply-chain tickets in Jira"*), the agent holds separate 3LO tokens per provider, and **each target platform enforces its own native security boundary** (BigQuery RLS, Snowflake Row Access Policies, and Jira Project Permissions) for that exact user!

---

## 5. Tough Customer Q&A Cheat Sheet (10 High-Probability Questions & Ready CE Answers)

### Q1: "Where are the user's OAuth refresh tokens stored, and how are they protected at rest?"
- **CE Answer**: Neither the ADK container code nor the LLM ever sees or stores the user's long-lived `refresh_token`. The `refresh_token` is encrypted at rest inside Google Cloud's managed credential vault—either **Discovery Engine's `Authorization` token store** (for Gemini Enterprise) or **IAM Auth Manager** (`agentidentitycredentials.googleapis.com`). When an agent turn starts, the managed vault performs the token refresh server-side and injects **only a short-lived (1-hour) Bearer `access_token`** (`ya29...`) into the ephemeral tool execution context. Customers requiring sovereign key control can also enable **CMEK (Customer-Managed Encryption Keys)** on Discovery Engine / Vertex AI.

### Q2: "What happens when the 1-hour Google OAuth `access_token` expires? Does the user get prompted every hour?"
- **CE Answer**: **No.** Because our Discovery Engine `Authorization` URI includes `access_type=offline&prompt=consent`, Google's OAuth server issues a long-lived `refresh_token` during the user's very first consent click. Whenever the 3,600-second `access_token` expires, Gemini Enterprise (or IAM Auth Manager) silently exchanges the stored `refresh_token` at `https://oauth2.googleapis.com/token` in the background and passes a fresh `access_token` to the agent—zero recurring popups for the user.
- **CE Gotcha to Watch Out For**: If the project's Google Auth Platform **Audience** is set to `External` and **Publishing status** is left in `Testing`, Google automatically expires refresh tokens after **7 days** (see [File 2 — Audience Deep Dive](02_GOOGLE_AUTH_PLATFORM_APPENDIX.md#4-deep-dive-how-the-audience-segment-links-to-everything-internal-vs-external)). Always use `Internal` audience for Workspace orgs!

### Q3: "How does a user or CISO revoke an agent's 3LO access immediately if an employee changes roles or leaves?"
- **CE Answer**: Defense-in-depth revocation works at **four levels**:
  1. **Role Change in Corporate IdP / Google Group**: If the employee moves from the North region to the West region, updating their Google Group membership immediately changes which BigQuery Row Access Policy matches `SESSION_USER()` on their very next query—no token revocation even needed!
  2. **User Self-Revocation**: The user can revoke the app anytime at `https://myaccount.google.com/permissions`.
  3. **Workspace Admin / CISO Revocation**: Workspace Admins can revoke OAuth tokens for any user or disable the OAuth Client ID globally in the Google Admin Console / Google Auth Platform (`console.cloud.google.com/auth/clients`).
  4. **Account Suspension**: Disabling the user in Google Workspace / Cloud Identity immediately invalidates all active OAuth access and refresh tokens across Google Cloud.

### Q4: "Does creating a new `bigquery.Client(credentials=...)` per tool call add latency or hit connection limits?"
- **CE Answer**: Constructing a `google.oauth2.credentials.Credentials(token=...)` and `bigquery.Client()` object in Python is a lightweight in-memory operation (~1–2 milliseconds) because it does not perform a network handshake on instantiation—the token is already minted! Compared to LLM reasoning (1–3 seconds) and BigQuery SQL execution (300–800 ms), per-request client instantiation adds **<0.2% overhead**. For high-throughput agents (hundreds of QPS), you can pass a shared `requests.Session` connection pool (`_http=shared_authorized_session_pool`) while still binding per-request `Credentials(token=user_token)` headers.

### Q5: "How does this architecture work inside a VPC Service Controls (VPC-SC) perimeter?"
- **CE Answer**: Both `aiplatform.googleapis.com` (Vertex AI Agent Runtime), `discoveryengine.googleapis.com` (Gemini Enterprise), and `bigquery.googleapis.com` are fully supported VPC-SC protected services. When all three reside in the same VPC-SC perimeter, no special egress rules are needed. If BigQuery datasets live in a separate Data Lake project perimeter while the Agent Runtime lives in an AI Platform perimeter, configure a **VPC-SC Ingress/Egress rule** allowing identity type `ANY_USER_ACCOUNT` (or specific Workspace groups) from the AI Platform project to access `bigquery.googleapis.com` in the Data Lake project.

### Q6: "In enterprise environments, our BigQuery data lives in Project B (`data-warehouse-prod`), while agents run in Project A (`ai-agents-prod`). Which project needs `roles/bigquery.jobUser` vs. `roles/bigquery.dataViewer`?"
- **CE Answer**: BigQuery cleanly separates **Compute (where the query job runs)** from **Storage (where the table lives)**:
  - **Project A (`ai-agents-prod` — Compute Project)**: Configure `BigQueryToolConfig(compute_project_id="ai-agents-prod")`. Grant the human users `roles/bigquery.jobUser` on **Project A** so all agent query compute is billed to the AI project.
  - **Project B (`data-warehouse-prod` — Storage Project)**: Grant the human users `roles/bigquery.dataViewer` on the dataset in **Project B**, and attach the **Row Access Policies** (`roles/bigquery.filteredDataViewer`) on the table in **Project B**. Users do **not** need `jobUser` on the production data warehouse project!

### Q7: "Can a clever user bypass BigQuery RLS through prompt injection, CTEs, `FOR SYSTEM_TIME AS OF` time travel, or error-based side channels (like `1 / 0`)?"
- **CE Answer**: **No.** BigQuery evaluates Row Access Policy filter predicates at the **storage scan layer prior to any user SQL operator**:
  - **Prompt Injection**: Even if the LLM is tricked into running `SELECT * FROM weekly_store_sales WHERE region = 'South'`, BigQuery rewrites the scan predicate to `WHERE (region = 'South') AND (region = 'North')`, returning `0 rows`.
  - **Division-by-Zero / Error Side Channels**: BigQuery guarantees that RLS predicates execute *before* user `WHERE` or `SELECT` expressions, preventing an attacker from probing hidden row values via `WHERE 1/(salary - 100000) = 1`.
  - **Time Travel (`FOR SYSTEM_TIME AS OF`)**: Non-admin filtered viewers cannot use time travel to read rows prior to the attachment of an RLS policy.

### Q8: "What if our users authenticate with Okta, Microsoft Entra ID (Azure AD), or Ping Identity instead of Google Workspace?"
- **CE Answer**: There are two standard patterns for external Enterprise IdPs:
  1. **Cloud Identity / Google Workspace Workforce Federation (Recommended)**: Most enterprise GCP customers federate Entra ID or Okta into **Google Cloud Identity** via SAML/OIDC + SCIM group sync (`uno@company.com`). In that setup, this exact repo works **unchanged**—when Google OAuth prompts `uno@company.com`, Google automatically redirects to Okta/Entra ID for SSO and mints the Google 3LO token!
  2. **Workforce Identity Federation (WIF) / External OIDC with IAM Auth Manager**: You can configure an external OIDC provider directly in IAM Auth Manager (`authProviders`) or Workforce Identity Pools (`principal://iam.googleapis.com/locations/global/workforcePools/...`), which BigQuery RLS also supports in `GRANT TO (...)`.

### Q9: "Can the agent combine 2LO (shared public lookup tables) and 3LO (restricted user tables) in the same conversation?"
- **CE Answer**: **Yes!** Because Plane 1 (the Reasoning Engine's own Service Account / SPIFFE Agent Identity) and Plane 2 (the user's 3LO OAuth token) coexist simultaneously inside the container:
  - Attach a **3LO `BigQueryToolset`** (using the user's OAuth token) for restricted tables like `retail_sales.weekly_store_sales`.
  - Attach a second **2LO Toolset** (using `google.auth.default()` or a 2LO `GcpAuthProviderScheme`) for shared company-wide resources like a public product catalog or macroeconomic weather forecast table where you don't want to grant individual IAM bindings to every user.

### Q10: "Can the LLM see the user's raw OAuth `access_token` or leak it in the chat response?"
- **CE Answer**: **No.** In both Mechanism A (`BigQueryToolset`) and Mechanism B (`McpToolset` / `AuthenticatedFunctionTool`), the OAuth token lives inside `tool_context.state` or `CredentialManager`—**outside** the LLM's conversation history and tool parameter schema. When Gemini decides to call `execute_sql(query="SELECT ...")`, the LLM only generates the `query` string; the Python runtime intercepts the tool execution, retrieves the token from state/vault, attaches the `Authorization: Bearer` header to the BigQuery HTTPS call, and returns only the resulting data rows to the LLM.
