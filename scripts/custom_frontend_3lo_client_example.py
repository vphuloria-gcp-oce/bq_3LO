#!/usr/bin/env python3
"""
Custom Frontend 3LO Integration Reference for Vertex AI Agent Runtime.

This script demonstrates the two official ways a Custom Frontend (outside Gemini Enterprise,
such as a React/Next.js SPA, Streamlit app, or FastAPI portal) can consume the deployed
BigQuery 3LO Agent Runtime while enforcing per-user Row-Level Security (RLS):

  Mode 1 (`--mode state-injection`, works directly with the deployed `app/agent.py`):
    - The custom frontend authenticates the end-user via Google OAuth 2.0 (scope:
      `https://www.googleapis.com/auth/bigquery`) and passes the user's short-lived
      access token in `session.state["bq-3lo-auth"]` (or `session.state["custom_access_token"]`)
      when streaming queries to the Vertex AI Reasoning Engine.
    - `_hybrid_3lo_get_valid_credentials` in `app/agent.py` automatically picks up the token
      from `tool_context.state`—meaning the exact same deployed Reasoning Engine serves both
      Gemini Enterprise AND your custom frontend without any code changes!

  Mode 2 (`--mode auth-manager-finalize`, for IAM Auth Manager v2 `GcpAuthProvider`):
    - Shows the exact `/validateUserId` (`credentials:finalize`) endpoint logic that a custom
      frontend server implements when using Google Cloud IAM Auth Manager (`agentidentity.googleapis.com/v1`).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any


def get_user_oauth_token_from_gcloud() -> str:
    """Helper for CLI testing: retrieves the active user's OAuth access token via ADC or gcloud."""
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        if creds.token:
            return creds.token.strip()
    except Exception:
        pass
    for cmd in (
        ["gcloud", "auth", "application-default", "print-access-token"],
        ["gcloud", "auth", "print-access-token"],
    ):
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    raise RuntimeError("Could not obtain an OAuth access token from ADC or gcloud.")


def invoke_agent_from_custom_frontend(
    project_id: str,
    location: str,
    reasoning_engine_id: str,
    user_id: str,
    user_oauth_token: str,
    prompt: str,
) -> None:
    """
    Demonstrates how a Custom Frontend invokes the deployed Agent Runtime
    while injecting the logged-in user's 3LO OAuth access token into session state.
    Uses the standard Vertex AI Reasoning Engine HTTP contract (:query and :streamQuery),
    which works identically from Python, Node/Next.js, Go, or any custom BFF.
    """
    resource_name = (
        reasoning_engine_id
        if reasoning_engine_id.startswith("projects/")
        else f"projects/{project_id}/locations/{location}/reasoningEngines/{reasoning_engine_id}"
    )
    base_url = f"https://{location}-aiplatform.googleapis.com/v1/{resource_name}"
    headers = {
        "Authorization": f"Bearer {user_oauth_token}",
        "Content-Type": "application/json",
    }

    print(f"🌐 Custom Frontend connecting to Agent Runtime: {resource_name}")
    print(f"👤 End-User Identity : {user_id}")
    print(f"💬 Prompt            : {prompt}\n")

    # 1. Create a session for the user and inject their 3LO token into state
    create_session_payload = json.dumps(
        {
            "class_method": "async_create_session",
            "input": {
                "user_id": user_id,
                "state": {
                    "bq-3lo-auth": user_oauth_token,
                    "custom_access_token": user_oauth_token,
                },
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}:query", data=create_session_payload, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        session_resp = json.loads(resp.read().decode("utf-8"))
    output = session_resp.get("output", session_resp)
    session_id = output["id"] if isinstance(output, dict) else str(output)
    print(f"✅ Created Agent Runtime Session: {session_id}\n🤖 Agent Response:\n")

    # 2. Stream the agent response via :streamQuery (async_stream_query)
    stream_payload = json.dumps(
        {
            "class_method": "async_stream_query",
            "input": {
                "user_id": user_id,
                "session_id": session_id,
                "message": prompt,
            },
        }
    ).encode("utf-8")
    s_req = urllib.request.Request(
        f"{base_url}:streamQuery", data=stream_payload, headers=headers, method="POST"
    )
    with urllib.request.urlopen(s_req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[len("data:") :].strip()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                content = event.get("content", {})
                for part in content.get("parts", []):
                    if fn := part.get("function_call"):
                        print(f"🛠️  [Tool Call] {fn.get('name')}({fn.get('args')})\n")
                    if text := part.get("text"):
                        print(text, end="", flush=True)
    print("\n")


def finalize_auth_manager_3lo_consent(
    project_id: str,
    location: str,
    auth_provider_id: str,
    consent_nonce: str,
    caller_bearer_token: str,
) -> dict[str, Any]:
    """
    Reference implementation of the `/validateUserId` (`FinalizeCredentials`) step
    required by Google Cloud IAM Auth Manager (v2 API) after the user grants OAuth consent.

    Reference: https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2#update-client
    """
    url = (
        f"https://agentidentitycredentials.googleapis.com/v1/"
        f"projects/{project_id}/locations/{location}/authProviders/{auth_provider_id}"
        f"/credentials:finalize"
    )
    payload = json.dumps({"consentNonce": consent_nonce}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {caller_bearer_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _default_reasoning_engine_id() -> str:
    meta_path = Path(__file__).resolve().parent.parent / "retail-sales-agent" / "deployment_metadata.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return data.get("remote_agent_runtime_id") or data.get("remote_agent_engine_id") or ""
        except Exception:
            pass
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Custom Frontend 3LO Client Example for Vertex AI Agent Runtime"
    )
    parser.add_argument("--project-id", default="your-gcp-project-id", help="GCP Project ID")
    parser.add_argument("--location", default="us-central1", help="Agent Runtime region")
    parser.add_argument(
        "--reasoning-engine-id",
        default=_default_reasoning_engine_id(),
        help="Reasoning Engine ID or full resource name (defaults to retail-sales-agent/deployment_metadata.json)",
    )
    parser.add_argument(
        "--mode",
        choices=["state-token", "auth-manager-finalize"],
        default="state-token",
        help="Consumption pattern to run (state-token or auth-manager-finalize)",
    )
    parser.add_argument("--user-id", default="custom-ui-user", help="End-user identifier")
    parser.add_argument(
        "--prompt",
        default="Which user am I authenticated as via SESSION_USER(), and which regions can I see?",
        help="Question to ask the agent",
    )
    args = parser.parse_args()
    if not args.reasoning_engine_id:
        parser.error("--reasoning-engine-id is required when deployment_metadata.json is not present.")

    token = get_user_oauth_token_from_gcloud()
    invoke_agent_from_custom_frontend(
        project_id=args.project_id,
        location=args.location,
        reasoning_engine_id=args.reasoning_engine_id,
        user_id=args.user_id,
        user_oauth_token=token,
        prompt=args.prompt,
    )


if __name__ == "__main__":
    main()
