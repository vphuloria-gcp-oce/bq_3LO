#!/usr/bin/env python3
"""
Pure-Python Vertex AI Agent Runtime Deployer with SPIFFE Agent Identity (`deploy/deploy_agent_runtime.py`).

Why this script exists:
  - If `agents-cli` (`google-agents-cli`) is installed on the workstation, `02_deploy_agent_and_ge.sh`
    uses it directly.
  - If `agents-cli` is NOT installed on a customer's machine, `02_deploy_agent_and_ge.sh`
    automatically invokes this script inside `retail-sales-agent/.venv` (`uv run python3 ../deploy/deploy_agent_runtime.py`),
    which uses the standard `vertexai` SDK (`google-cloud-aiplatform[agent-engines]`) already declared
    in `retail-sales-agent/pyproject.toml` to deploy the container with `IdentityType.AGENT_IDENTITY`
    and write `retail-sales-agent/deployment_metadata.json`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import urllib.error
import urllib.request
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning, module="google.cloud.aiplatform")

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO_ROOT / "retail-sales-agent"
METADATA_FILE = AGENT_DIR / "deployment_metadata.json"

DEFAULT_IGNORE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".adk",
    ".google-agents-cli",
    ".ruff_cache",
}


def _collect_source_packages(agent_dir: Path) -> list[str]:
    files: list[str] = []
    for root, dirs, filenames in os.walk(agent_dir):
        dirs[:] = sorted(d for d in dirs if d not in DEFAULT_IGNORE_DIRS)
        rel_dir = Path(root).relative_to(agent_dir)
        for fname in sorted(filenames):
            if fname in {".DS_Store", ".env", "deployment_metadata.json"} or fname.startswith("client_secret"):
                continue
            rel_path = (rel_dir / fname).as_posix()
            files.append(f"./{rel_path}")
    return files


def _bind_plane1_iam(project_id: str, agent_runtime_id: str) -> None:
    manage_script = REPO_ROOT / "deploy" / "manage_ge_app.py"
    subprocess.run(
        [
            "python3",
            str(manage_script),
            "--project-id",
            project_id,
            "--bind-agent-identity",
            agent_runtime_id,
        ],
        check=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy ADK Agent to Vertex AI Agent Runtime with SPIFFE AGENT_IDENTITY using the vertexai SDK."
    )
    parser.add_argument("--project-id", required=True, help="GCP Project ID")
    parser.add_argument("--region", default="us-central1", help="GCP Region (default: us-central1)")
    parser.add_argument("--service-name", default="retail-sales-agent-id", help="Reasoning Engine display name")
    parser.add_argument(
        "--description",
        default="OmniRetail BigQuery 3LO Sales Agent with SPIFFE Agent Identity",
        help="Reasoning Engine description",
    )
    args = parser.parse_args()

    os.chdir(AGENT_DIR)

    import vertexai
    from vertexai._genai import _agent_engines_utils
    from vertexai._genai.types import AgentEngineConfig, IdentityType

    client = vertexai.Client(
        project=args.project_id,
        location=args.region,
        http_options={"api_version": "v1beta1"},
    )
    vertexai.init(project=args.project_id, location=args.region)

    existing_agents = list(client.agent_engines.list())
    matching = [
        ag for ag in existing_agents if getattr(ag.api_resource, "display_name", "") == args.service_name
    ]
    is_update = bool(matching)

    if not matching:
        print(f"🪪 Creating SPIFFE AGENT_IDENTITY Reasoning Engine '{args.service_name}' in {args.region}...")
        bare_agent = client.agent_engines.create(
            config={
                "identity_type": IdentityType.AGENT_IDENTITY,
                "display_name": args.service_name,
            }
        )
        matching = [bare_agent]
        _bind_plane1_iam(args.project_id, bare_agent.api_resource.name)

    resource_name = matching[0].api_resource.name
    source_packages = _collect_source_packages(AGENT_DIR)

    env_vars = {
        "AGENT_VERSION": "0.1.0",
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "true",
        "APP_URL": f"https://{args.region}-aiplatform.googleapis.com/reasoningEngines/v1/{resource_name}/api",
        "BQ_PROJECT_ID": args.project_id,
        "BQ_DATASET": os.environ.get("BQ_DATASET", "retail_sales"),
        "BQ_TABLE": os.environ.get("BQ_TABLE", "weekly_store_sales"),
        "BQ_LOCATION": os.environ.get("BQ_LOCATION", "US"),
        "GEMINI_AUTH_ID": os.environ.get("GEMINI_AUTH_ID", "bq-3lo-auth"),
    }
    if os.environ.get("OAUTH_CLIENT_ID"):
        env_vars["OAUTH_CLIENT_ID"] = os.environ["OAUTH_CLIENT_ID"]
    if os.environ.get("OAUTH_CLIENT_SECRET"):
        env_vars["OAUTH_CLIENT_SECRET"] = os.environ["OAUTH_CLIENT_SECRET"]

    config = AgentEngineConfig(
        display_name=args.service_name,
        description=args.description,
        source_packages=source_packages,
        env_vars=env_vars,
        identity_type=IdentityType.AGENT_IDENTITY,
        agent_framework="google-adk",
        image_spec={},
        min_instances=1 if not is_update else None,
        max_instances=10 if not is_update else None,
        container_concurrency=9 if not is_update else None,
        resource_limits={"cpu": "4", "memory": "8Gi"} if not is_update else None,
    )

    print(f"🚀 Building and deploying container to {resource_name} (takes ~5-8 minutes)...")
    api_config = client.agent_engines._create_config(
        mode="update",
        display_name=config.display_name,
        description=config.description,
        source_packages=config.source_packages,
        env_vars=config.env_vars,
        identity_type=config.identity_type,
        agent_framework=config.agent_framework,
        image_spec=config.image_spec,
        min_instances=config.min_instances,
        max_instances=config.max_instances,
        resource_limits=config.resource_limits,
        container_concurrency=config.container_concurrency,
    )
    operation = client.agent_engines._update(name=resource_name, config=api_config)
    print(f"   Operation started: {operation.name}")
    _agent_engines_utils._await_operation(
        operation_name=operation.name,
        get_operation_fn=client.agent_engines._get_agent_operation,
    )
    completed_op = client.agent_engines._get_agent_operation(operation_name=operation.name)
    if completed_op.error:
        raise RuntimeError(f"Agent Runtime deployment failed: {completed_op.error}")

    remote_agent = client.agent_engines.get(name=resource_name)
    metadata = {
        "remote_agent_runtime_id": remote_agent.api_resource.name,
        "deployment_target": "agent_runtime",
        "is_a2a": True,
        "agent_directory": "app",
        "deployment_timestamp": datetime.datetime.now(tz=datetime.UTC).isoformat(),
    }
    METADATA_FILE.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"✅ Deployed Agent Runtime ID written to {METADATA_FILE.name}: {remote_agent.api_resource.name}")


if __name__ == "__main__":
    main()
