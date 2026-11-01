# 🔐 Appendix & Reference: Google Auth Platform, Branding, Audience & IAM Auth Manager

> **🔒 INTERNAL GOOGLE CLOUD CUSTOMER ENGINEERING (CE) PLAYBOOK**
> **Navigation**: [01: Architecture POV & Tough Q&A](01_CE_ARCHITECTURE_POV_AND_TOUGH_QA.md) · **02: Google Auth Platform, Branding & Audience Appendix** · [03: Internal Demo Playbook, Antigravity Builder & Rollback Runbook](03_INTERNAL_DEMO_AND_BUILDER_RUNBOOK.md)

---

When walking enterprise customers through the **Google Cloud Console** during a 3-Legged OAuth (3LO) setup, questions inevitably arise around the **Google Auth Platform** (`console.cloud.google.com/auth`—formerly known as the "OAuth Consent Screen" and "APIs & Services → Credentials"):
- *"What does the Google Auth Platform actually do vs. Gemini Enterprise Authorizations vs. IAM Auth Manager?"*
- *"Is the **Branding** configuration only aesthetic for what the user sees on the popup screen, or does it actually enforce security and functional gates?"*
- *"How does the **Audience** setting (`Internal` vs. `External`) connect to our Workspace organization, OAuth verification, and refresh token lifetimes?"*

This appendix gives Customer Engineers a definitive reference to answer all three questions with precision.

---

## 1. What the Authentication Service Does & Where It Fits in the Overall Flow

Customers frequently conflate **OAuth Client Creation**, **Token Vaulting/Brokering**, and **Tool Execution**. In reality, a Google Cloud 3LO agent architecture cleanly separates these concerns across **three distinct architectural layers**:

```mermaid
flowchart TB
    subgraph Layer1["LAYER 1: Identity Provider & OAuth Policy Server (Google Auth Platform — console.cloud.google.com/auth)"]
        Brand["🎨 Branding (App Name, Logo, Support Email, Authorized Domains)"]
        Audience["👥 Audience (Internal Workspace Org vs. External Testing/Prod)"]
        Scopes["🛡️ Data Access / Scopes (e.g., .../auth/bigquery)"]
        OAuthClient["🔑 OAuth 2.0 Web Client (client_id + client_secret + Authorized redirect_uris)"]
        Brand --> Audience --> Scopes --> OAuthClient
    end

    subgraph Layer2["LAYER 2: Relying Party Credential Broker & Token Vault (Holds Secret & Refresh Tokens)"]
        direction LR
        BrokerA["Option A: Gemini Enterprise Authorization (discoveryengine.googleapis.com/.../authorizations/bq-3lo-auth)"]
        BrokerB["Option B: IAM Auth Manager (agentidentity.googleapis.com/.../authProviders/bq-3lo)"]
    end

    subgraph Layer3["LAYER 3: Agent Runtime & Per-Turn Tool Execution (Vertex AI Agent Engine)"]
        Resolver["ADK GoogleCredentialsManager / CredentialManager (Receives short-lived 1-hr ya29... access_token)"]
        Tool["BigQueryToolset / McpToolset / AuthenticatedFunctionTool"]
        Resolver --> Tool
    end

    subgraph Target["Downstream Resource Server"]
        BQ[("BigQuery API (Verifies ya29... token against Layer 1 & enforces RLS for SESSION_USER())")]
    end

    OAuthClient ==>|"1. Provides client_id & client_secret"| Layer2
    Layer2 <==>|"2. Redirects user to /o/oauth2/v2/auth & exchanges code at /token (Stores encrypted refresh_token)"| Layer1
    Layer2 ==>|"3. Injects short-lived Bearer access_token per turn"| Resolver
    Tool ==>|"4. Authorization: Bearer ya29..."| BQ
```

### Detailed Responsibilities of the Three Layers

| Layer | Service Name & Console / API Endpoint | Core Responsibility in the 3LO Lifecycle | Does It Store User Refresh Tokens? |
| :--- | :--- | :--- | :--- |
| **Layer 1: OAuth 2.0 Authorization Server / IdP Config** | **Google Auth Platform**<br/>(`console.cloud.google.com/auth/*` & `accounts.google.com` / `oauth2.googleapis.com`) | • Defines **who** the requesting app is (**Branding**).<br/>• Defines **which user population** is allowed to sign in (**Audience**: `Internal` vs. `External`).<br/>• Defines **what permissions** can be requested (**Scopes**).<br/>• Issues the **OAuth 2.0 Web Client** (`client_id` + `client_secret`) and strictly enforces the **Authorized Redirect URI allowlist**. | **Issues** and **validates** tokens (and lists active grants at `myaccount.google.com/permissions`), but delegates token storage to the Relying Party (Layer 2). |
| **Layer 2: Credential Broker / Token Vault (Relying Party)** | **Gemini Enterprise `Authorizations`**<br/>(`discoveryengine.googleapis.com/v1alpha/.../authorizations`)<br/>**OR**<br/>**IAM Auth Manager**<br/>(`agentidentity.googleapis.com/v1/.../authProviders`) | • Securely vaults your OAuth `client_id` and `client_secret`.<br/>• Initiates the 302 redirect to Google's `/authorize` endpoint when a user invokes a protected tool.<br/>• Receives the one-time authorization `code` at its callback (`/oauth-redirect` or `/oauthcallback`).<br/>• Exchanges the `code` + `client_secret` at `oauth2.googleapis.com/token` and **encrypts & stores the user's `refresh_token`**. | **Yes.** Encrypts and stores `(user_identity, auth_id) -> refresh_token` server-side and automatically mints fresh 1-hour `access_token`s on future turns. |
| **Layer 3: Agent Runtime & Tool Execution** | **ADK `GoogleCredentialsManager` / `CredentialManager`**<br/>(`retail-sales-agent` on Vertex AI Agent Runtime) | • Receives **only** the short-lived (3,600-second) Bearer `access_token` (`ya29...`) for the active user turn.<br/>• Attaches `Authorization: Bearer ya29...` to the outbound BigQuery API request (`jobs.query`). | **No.** Stateless per turn; never persists user refresh tokens or OAuth client secrets in user session logs. |

---

## 2. How It Connects Across Agentic vs. Non-Agentic Workloads

A powerful way to demystify 3LO for enterprise security teams is to show that **AI Agents do not invent a proprietary, unproven authentication protocol**. Instead, they use the **exact same Layer 1 Google Auth Platform primitives** (Branding, Audience, Scopes, and OAuth 2.0 Web Clients) that enterprises have trusted for over a decade in web apps and BI platforms:

| Workload Category | Layer 1: Google Auth Platform (`Branding`, `Audience`, `OAuth Client`) | Layer 2: Who Acts as the OAuth Relying Party (Token Vault) & Redirect URI? | Layer 3: How the Downstream API Uses the 3LO Token |
| :--- | :--- | :--- | :--- |
| **1. Traditional Web Apps / SPAs**<br/>*(e.g., Custom React + FastAPI / Spring Boot Portal)* | Same project Brand, `Internal` Audience, and OAuth 2.0 Web Client (`client_id` / `client_secret`). | **App Backend (BFF)**.<br/>Redirect URI: `https://app.company.com/oauth2/callback`.<br/>Stores refresh tokens in Redis / Cloud SQL session store. | Backend passes `Credentials(token=...)` when calling Google Drive, Calendar, or BigQuery APIs on behalf of the logged-in web user. |
| **2. BI & Analytics Tools**<br/>*(Looker, Looker Studio, Connected Sheets, Tableau)* | Google-managed or customer-managed OAuth Client requesting `.../auth/bigquery`. | **BI Platform Server** (e.g., Looker server or Looker Studio OAuth broker).<br/>Stores per-analyst OAuth tokens when "Run queries as viewer (OAuth)" is enabled on the connection. | Every dashboard tile query runs in BigQuery with the viewing analyst's 3LO token—enforcing the exact same `SESSION_USER()` BigQuery Row Access Policies! |
| **3. AI Agents in Gemini Enterprise**<br/>*(Mechanism A — Used in [`app/agent.py`](../retail-sales-agent/app/agent.py))* | Project Brand + `Internal` Audience + OAuth 2.0 Web Client (`bq-3lo-agent-web-client`). | **Discovery Engine `Authorization` Resource** (`bq-3lo-auth`).<br/>Redirect URI: `https://vertexaisearch.cloud.google.com/oauth-redirect`. | Gemini Enterprise injects `state["bq-3lo-auth"] = "ya29..."` into the ADK Reasoning Engine call; `BigQueryToolset` runs SQL as `SESSION_USER()`. |
| **4. Custom AI Agents on Agent Runtime / Cloud Run / GKE**<br/>*(Mechanism B — Used in [`app/agent_auth_manager_example.py`](../retail-sales-agent/app/agent_auth_manager_example.py))* | Project Brand + `Internal` Audience + OAuth 2.0 Web Client (or 3rd-party Snowflake/Jira OAuth Client). | **IAM Auth Manager (`authProviders`)**.<br/>Redirect URI: `https://agentidentitycredentials.googleapis.com/v1/projects/.../authProviders/.../oauthcallback` + `/validateUserId`. | `GcpAuthProvider` retrieves `ya29...` from Auth Manager and passes it to `McpToolset` (`bigquery.googleapis.com/mcp`) or `AuthenticatedFunctionTool`. |

---

## 3. Deep Dive: How Branding Configuration Affects the Experience (Aesthetic vs. Functional Impact)

When customers open **Google Auth Platform → Branding** (`https://console.cloud.google.com/auth/branding?project=<PROJECT_ID>`), they often ask:
> *"Is Branding just cosmetic for what the user sees on the OAuth popup, or does changing Branding actually affect functionality and security?"*

**CE Answer**: Branding has **both** aesthetic/UX elements **and three hard functional/security enforcement gates** that will break your OAuth flow if misconfigured!

### Aesthetic vs. Functional Impact of Branding Configuration

| Branding Field (`console.cloud.google.com/auth/branding`) | Aesthetic / User-Facing UX Impact | Hard Functional & Security Impact (Non-Aesthetic!) |
| :--- | :--- | :--- |
| **1. Existence of the Brand Resource**<br/>(`iap.googleapis.com/v1/projects/{N}/brands`) | None until configured. | **🛑 Hard API Prerequisite Gate (`400 FAILED_PRECONDITION`)**: Google Cloud **blocks** the creation of any OAuth 2.0 Client ID in a GCP project until the project's Brand (Consent Screen) is initialized. *(This is why [`deploy/manage_ge_app.py`](../deploy/manage_ge_app.py) automatically calls `POST https://iap.googleapis.com/v1/projects/<NUM>/brands` to initialize the Brand before you create the Web Client!)* |
| **2. App Name**<br/>*(e.g., `"OmniRetail 3LO Sales Assistant"`)* | Displayed prominently on the Google Sign-In & OAuth Consent Screen (*"**OmniRetail 3LO Sales Assistant** wants to access your Google Account"*) and in the user's Google Account Security audit list (`myaccount.google.com/permissions`). | • **Anti-Impersonation Policy Enforcement**: For `External` apps, App Names cannot contain `"Google"`, `"BigQuery"`, or misleading system terms without failing OAuth Brand Verification.<br/>• **Audit Trail Identification**: Security admins reviewing Workspace OAuth Token Audit Logs see this exact App Name alongside the Client ID. |
| **3. User Support Email**<br/>*(e.g., `admin@vphuloria.altostrat.com`)* | Shown to end users when they click the app name on the consent screen so they know which internal team owns the agent. | **🛑 Mandatory Creation Validation**: The caller creating/updating the Brand **must** either be the exact email address entered here or be a manager/member of the specified Google Group; otherwise the API rejects the Brand creation with `400 INVALID_ARGUMENT`. |
| **4. App Logo** | Renders a custom 120×120px icon on the consent prompt. | **⚠️ Triggers Mandatory Manual Google Trust & Safety Verification on `External` Apps!** Uploading an App Logo on an `External` In-Production app immediately places the Brand into a verification queue (takes several days and requires domain ownership proof in Google Search Console) before the logo is shown. *(Tip: Leave App Logo blank for rapid PoCs, or use `Internal` Audience!)* |
| **5. Authorized Domains**<br/>*(e.g., `google.com`, `yourcompany.com`)* | Not directly visible to the end user. | **🛑 Strict Redirect URI & Link Allowlist Enforcement (For `External` Apps)**: Google's OAuth server validates that the domain of every **Authorized Redirect URI** on your OAuth Web Clients (as well as your App Homepage, Privacy Policy, and Terms of Service URLs) belongs to the **Authorized Domains** list in Branding. If a domain is missing from Authorized Domains, the Console refuses to save the OAuth Client's Redirect URI! *(Note: `Internal` apps automatically trust internal/localhost/Google redirect URIs without manual domain verification.)* |
| **6. App Homepage, Privacy Policy & Terms of Service Links** | Shown as clickable footer links on the consent screen. | **🛑 Required for OAuth Verification (`External` Production Apps)**: Cannot submit an `External` app requesting sensitive scopes (`.../auth/bigquery`) for Google verification unless these URLs are publicly reachable and hosted on a verified **Authorized Domain**. |

---

## 4. Deep Dive: How the Audience Segment Links to Everything (`Internal` vs. `External`)

The single most consequential architectural switch in **Google Auth Platform → Audience** (`https://console.cloud.google.com/auth/audience?project=<PROJECT_ID>`) is choosing between **`Internal`** and **`External`**.

This setting directly connects your **GCP Project hierarchy**, your **Google Workspace / Cloud Identity Organization**, your **OAuth Scopes (`.../auth/bigquery`)**, and your **Refresh Token expiration lifecycle**:

```mermaid
flowchart TD
    Start["Google Auth Platform → Audience (console.cloud.google.com/auth/audience)"] --> Choice{"User Type Selection"}

    Choice -- "1. INTERNAL (Recommended for Enterprise & Argolis)" --> IntOrg["Bound to GCP Project's Google Workspace / Cloud Identity Organization (e.g., @vphuloria.altostrat.com)"]
    IntOrg --> IntUsers["✅ Any user inside the Workspace Org gets a clean 1-click consent screen"]
    IntOrg --> IntBlock["🚫 Any user outside the Workspace Org is hard-blocked with '403: org_internal'"]
    IntOrg --> IntScopes["🚀 Sensitive & Restricted Scopes (e.g., .../auth/bigquery) work IMMEDIATELY with ZERO Google App Verification!"]
    IntOrg --> IntRefresh["♾️ Refresh Tokens do NOT expire after 7 days (long-lived until revoked)"]

    Choice -- "2. EXTERNAL (For B2C, Multi-Domain Partners, or Projects without an Org)" --> ExtMode{"Publishing Status"}

    ExtMode -- "2A. Testing (Default)" --> ExtTestUsers["⚠️ ONLY explicitly listed 'Test Users' (max 100 emails) can sign in; all others get '403: access_denied'"]
    ExtTestUsers --> ExtTest7Day["🧨 CRITICAL GOTCHA: Refresh Tokens AUTOMATICALLY EXPIRE AFTER 7 DAYS! Users must re-consent every week!"]

    ExtMode -- "2B. In Production" --> ExtProd["Open to any Google Account (@gmail.com or external domains)"]
    ExtProd --> ExtProdVerify["🛡️ Requesting '.../auth/bigquery' (Sensitive Scope) shows 'Google hasn't verified this app' warning screen (or blocks login) until formal Google OAuth Verification is completed!"]
```

### Complete Comparison: `Internal` vs. `External (Testing)` vs. `External (In Production)`

| Dimension | `Internal` *(Used in This Repo)* | `External` — `Testing` Status | `External` — `In Production` Status |
| :--- | :--- | :--- | :--- |
| **Prerequisite on GCP Project** | Project **must** belong to a **Google Cloud Organization** (backed by Google Workspace or Cloud Identity). | Works on any GCP project (including standalone projects with "No Organization"). | Works on any GCP project. |
| **Who Can Complete the 3LO Flow?** | **Only** users whose Google identity belongs to the owning Workspace / Cloud Identity Organization (e.g., `admin@`, `uno@`, `dos@`, `tres@` in `vphuloria.altostrat.com`). | **Only** the specific email addresses manually added to the **Test users** list on the Audience page (hard cap of **100 users**). | Any Google Account (`@gmail.com` or any external Workspace domain). |
| **Error Shown to Unauthorized Users** | **`Error 403: org_internal`**<br/>*"This client is restricted to users within its organization."* | **`Error 403: access_denied`**<br/>*"<App> has not completed the Google verification process. The app is currently being tested, and can only be accessed by developer-approved testers."* | N/A (unless blocked by an unverified sensitive/restricted scope policy). |
| **Impact on Sensitive Scopes (`https://www.googleapis.com/auth/bigquery`)** | **Zero Friction**: No Google Trust & Safety review required, and no "Unverified App" warning screen is shown. Users see a clean, trusted enterprise consent card. | **Allowed for Test Users**, though users see an unverified developer test notice during consent. | **Triggers Mandatory Google OAuth App Verification!** Until Google reviews and approves your app (3–7+ business days), external users face the scary **"Google hasn't verified this app"** warning screen—or are blocked completely if their Workspace admin disables unverified 3rd-party apps! |
| **Impact on Restricted Scopes *(e.g., `gmail.readonly`, `drive` full)*** | **Zero External Review**: Allowed immediately within your own Workspace org. | Allowed only for listed Test Users. | Requires both Google OAuth App Verification **and** an annual 3rd-party **CASA (Cloud Application Security Assessment)** security audit. |
| **OAuth `refresh_token` Lifespan** | **Long-Lived**: Does not expire unless the user revokes access, the password/account is reset, or an admin sets a Workspace session-length policy. | **🧨 EXPIRES EVERY 7 DAYS!** Google's OAuth server hard-caps refresh tokens for `External (Testing)` apps at **604,800 seconds (7 days)**. Every 7 days, `oauth2.googleapis.com/token` returns `invalid_grant`, forcing users to re-click **Authorize**! | **Long-Lived** (subject to standard Google 100-refresh-token-per-client-per-user limit and 6-month inactivity expiration). |

---

### Practical Troubleshooting Guide for CEs: Diagnosing Google Auth Platform Errors

When a customer or workshop attendee encounters an OAuth error popup during the 3LO flow, use the exact error code on the Google Sign-In screen to pinpoint the misconfiguration in <15 seconds:

| OAuth Error Code on Screen | Root Cause Layer | Exact Fix in Google Cloud Console |
| :--- | :--- | :--- |
| **`Error 400: redirect_uri_mismatch`** | **Layer 1: OAuth Client (`Clients`)** | Open `console.cloud.google.com/auth/clients?project=<PROJECT_ID>` → click your Web Client → add the exact `redirect_uri` shown in the error details (`https://vertexaisearch.cloud.google.com/oauth-redirect` for Gemini Enterprise, or `http://localhost:8000/dev-ui/` with trailing slash for `adk web`). Wait 60–120 seconds for propagation. |
| **`Error 403: org_internal`** | **Layer 1: Audience (`Internal`)** | The user signed into Chrome with a `@gmail.com` or `@google.com` profile instead of a user inside the project's Workspace domain (`@<USER_DOMAIN>`). Either sign in with `uno@<USER_DOMAIN>`, or if you genuinely need cross-domain access, switch **Audience** to `External` and add the email under **Test users**. |
| **`Error 403: access_denied`** *(App is currently being tested)* | **Layer 1: Audience (`External — Testing`)** | Your project's Audience is set to `External (Testing)` and the signed-in email is not in the **Test users** table. Open `console.cloud.google.com/auth/audience?project=<PROJECT_ID>` and either switch User Type to **`Internal`** (recommended) or click **+ Add Users** under Test users. |
| **`Error 401: invalid_client`** *(or token exchange fails silently after clicking Allow)* | **Layer 2: Discovery Engine `Authorization` / IAM Auth Manager** | The `clientSecret` stored in `bq-3lo-auth` (or `app/oauth_config.json`) does not match the active secret on the OAuth Client in Google Auth Platform. Download a fresh `client_secret_*.json` into the repo root and re-run `./deploy/02_deploy_agent_and_ge.sh`. |
| **Users are forced to re-click `Authorize` once a week** | **Layer 1: Audience (`External — Testing`) OR Layer 2 (`authorizationUri`)** | 1. Verify **Audience** is `Internal` (not `External — Testing`, which enforces a 7-day refresh token TTL).<br/>2. Verify the Discovery Engine `Authorization` `authorizationUri` includes `access_type=offline&prompt=consent` so Google issues a `refresh_token`. |
