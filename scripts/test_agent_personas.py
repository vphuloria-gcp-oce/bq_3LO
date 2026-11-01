#!/usr/bin/env python3
"""
End-to-End 3LO Agent Persona Tester (`scripts/test_agent_personas.py`).

Tests the ADK Retail Sales Agent (`retail-sales-agent/app/agent.py`) by injecting a user's
OAuth 2.0 access token into the ADK session state (`state={GEMINI_AUTH_ID: "<token>"}`)—mirroring
Gemini Enterprise 3-Legged OAuth token propagation.

Usage:
  (cd retail-sales-agent && uv run --no-sync python3 ../scripts/test_agent_personas.py)
  (cd retail-sales-agent && uv run --no-sync python3 ../scripts/test_agent_personas.py --account uno@vphuloria.altostrat.com)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "retail-sales-agent"))


def get_oauth_token(account: str | None = None) -> str:
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

    if account:
        res = subprocess.run(["gcloud", "auth", "print-access-token", account], capture_output=True, text=True, env=env)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        raise RuntimeError(f"Account '{account}' is not logged in. Run: gcloud auth login {account}")

    for cmd in (["gcloud", "auth", "print-access-token"], ["gcloud", "auth", "application-default", "print-access-token"]):
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    raise RuntimeError("No active gcloud or ADC token found.")


async def run_agent_with_3lo_token(prompt: str, access_token: str) -> None:
    os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"
    os.environ["GOOGLE_API_USE_MTLS_ENDPOINT"] = "never"
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from app.agent import SETTINGS, root_agent

    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", SETTINGS["PROJECT_ID"])
    runner = InMemoryRunner(agent=root_agent, app_name="bq_3lo_tester")
    session = await runner.session_service.create_session(
        app_name="bq_3lo_tester",
        user_id="demo_user",
        state={SETTINGS["GEMINI_AUTH_ID"]: access_token},
    )

    print("=" * 86)
    print(f"🗣️  USER PROMPT: {prompt}")
    print("=" * 86)

    msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    async for event in runner.run_async(user_id="demo_user", session_id=session.id, new_message=msg):
        for part in (event.content.parts if event.content and event.content.parts else []):
            if part.function_call:
                print(f"🛠️  [Tool Call] {part.function_call.name}({dict(part.function_call.args)})")
            elif part.function_response:
                preview = str(part.function_response.response)
                print(f"📥 [Tool Result] {part.function_response.name} -> {preview[:260] + ('...}' if len(preview) > 260 else '')}")
            elif part.text:
                print(f"\n🤖 AGENT RESPONSE:\n{part.text.strip()}\n")
    print("=" * 86)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test the ADK 3LO BigQuery Agent with a user's OAuth token.")
    parser.add_argument("--account", help="Optional credentialed email (e.g. uno@...) to fetch OAuth token for.")
    parser.add_argument(
        "--prompt",
        default="Which user am I logged in as in BigQuery, which region(s) and stores can I see, and why did my stores beat or miss their weekly target?",
        help="Question to ask the ADK Agent.",
    )
    args = parser.parse_args()
    asyncio.run(run_agent_with_3lo_token(args.prompt, get_oauth_token(args.account)))


if __name__ == "__main__":
    main()
