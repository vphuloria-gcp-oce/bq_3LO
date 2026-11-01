# OmniRetail BigQuery 3LO Sales Agent (`retail-sales-agent`)

Google ADK agent configured with `BigQueryToolset` and 3-Legged OAuth (3LO) to enforce per-user BigQuery Row-Level Security (RLS) across Gemini Enterprise and ADK Web.

## Directory Layout

```text
retail-sales-agent/
├── app/
│   ├── agent.py          # BigQueryToolset + Hybrid 3LO credential resolver + GlobalGemini
│   ├── fast_api_app.py   # FastAPI / A2A / Reasoning Engine server entrypoint
│   └── app_utils/        # Telemetry, session, and A2A/Agent Engine adapters
├── tests/                # Unit and integration tests
├── Dockerfile            # Container build for Vertex AI Agent Runtime
└── pyproject.toml        # Python dependencies managed via uv
```

## Local Development & Verification

```bash
# Verify FastAPI & ADK agent import
uv run --no-sync python3 -c "import app.fast_api_app; print('OK')"

# Run unit tests
uv run --no-sync pytest tests/unit

# Launch interactive ADK Web UI (triggers 3LO OAuth consent flow)
uv run adk web
```

For end-to-end cloud deployment and Gemini Enterprise publishing, run `../deploy/02_deploy_agent_and_ge.sh` from the repository root.
