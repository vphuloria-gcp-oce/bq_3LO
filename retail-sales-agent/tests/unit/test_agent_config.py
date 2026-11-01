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

"""Lightweight unit tests for 3LO BigQuery agent configuration and token extraction."""

from types import SimpleNamespace

import pytest

from app.agent import (
    FQ_TABLE,
    SETTINGS,
    _extract_token,
    _hybrid_3lo_get_valid_credentials,
    root_agent,
)


def test_agent_settings_and_table() -> None:
    assert not SETTINGS["PROJECT_ID"].isdigit()
    assert FQ_TABLE.endswith(f".{SETTINGS['BQ_DATASET']}.{SETTINGS['BQ_TABLE']}")
    assert root_agent.name == "root_agent"


def test_extract_token_formats() -> None:
    assert _extract_token("ya29.sample_token ") == "ya29.sample_token"
    assert _extract_token({"access_token": "ya29.dict_token"}) == "ya29.dict_token"
    assert _extract_token({"oauth2": {"access_token": "ya29.nested"}}) == "ya29.nested"
    assert _extract_token({}) is None


@pytest.mark.asyncio
async def test_hybrid_3lo_resolves_gemini_enterprise_state_tokens() -> None:
    auth_id = SETTINGS["GEMINI_AUTH_ID"]
    ctx_direct = SimpleNamespace(state={auth_id: "ya29.direct_ge_token"})
    creds_direct = await _hybrid_3lo_get_valid_credentials(None, ctx_direct)  # type: ignore[arg-type]
    assert creds_direct is not None and creds_direct.token == "ya29.direct_ge_token"

    full_res = f"projects/12345/locations/global/authorizations/{auth_id}"
    ctx_res = SimpleNamespace(state={full_res: {"access_token": "ya29.resource_token"}})
    creds_res = await _hybrid_3lo_get_valid_credentials(None, ctx_res)  # type: ignore[arg-type]
    assert creds_res is not None and creds_res.token == "ya29.resource_token"
