"""Offline unit tests for the Starter repository (plugin loader, agent build, eval loader)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from app.agent import build_agent
from app.loader import (
    assert_no_state_tokens,
    hello_expedition_status,
    load_seed_pack,
    resolve_pack_directory,
    sanitize_instruction,
)
from eval.run_eval import (
    discover_eval_files,
    load_eval_cases,
    score_response_match,
    score_tool_trajectory,
)


def test_fallback_hello_pack_when_seed_pack_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """When SEED_PACK is unset, loader returns the generic hello_fallback pack."""
    monkeypatch.delenv("SEED_PACK", raising=False)
    pack = load_seed_pack("")
    assert pack.pack_id == "hello_fallback"
    assert pack.mcp_stdio_attached is False
    assert len(pack.tools) == 1
    out = json.loads(hello_expedition_status("Table 8"))
    assert out["status"] == "ONLINE"
    assert out["table_name"] == "Table 8"


def test_load_t00_example_pack_and_prefix_resolution() -> None:
    """Loader resolves both exact ('t00_example') and prefix ('t00') pack directories."""
    resolved = resolve_pack_directory("t00")
    assert resolved.name == "t00_example"

    pack_plain = load_seed_pack("t00_example", enable_mcp_stdio=False, strict_state_tokens=True)
    assert pack_plain.pack_id == "t00_example"
    assert pack_plain.bigquery_dataset == "seed_t00"
    assert pack_plain.mcp_stdio_attached is False
    tool_names = [t.__name__ for t in pack_plain.tools if callable(t)]
    assert "lookup_coffee_menu" in tool_names
    assert "get_roastery_sales_stats" in tool_names
    assert "submit_bean_reorder" in tool_names
    assert len(tool_names) == len(set(tool_names)), "Duplicate tool names must never be registered"


def test_load_t00_example_with_mcp_stdio_deduplicates_tools() -> None:
    """When MCP stdio is enabled, McpToolset is attached and plain duplicate MCP tools are filtered."""
    pack_mcp = load_seed_pack("t00_example", enable_mcp_stdio=True)
    assert pack_mcp.mcp_stdio_attached is True
    fn_names = [t.__name__ for t in pack_mcp.tools if callable(t)]
    assert "lookup_coffee_menu" in fn_names
    assert "get_roastery_sales_stats" in fn_names
    # submit_bean_reorder is served via McpToolset when stdio is attached
    assert "submit_bean_reorder" not in fn_names
    assert len(pack_mcp.tools) == 3


def test_state_token_guard_and_sanitizer() -> None:
    """Instruction state-token guard catches raw {var} tokens while allowing {user:*}."""
    assert_no_state_tokens("Clean prompt with {user:preferred_lang} and no bare braces.")
    with pytest.raises(ValueError, match="session-state tokens"):
        assert_no_state_tokens("Bad prompt with {json_payload} inside.")

    sanitized = sanitize_instruction("Order {order_id} for {user:name}")
    assert sanitized == "Order [order_id] for {user:name}"
    assert_no_state_tokens(sanitized)


def test_agent_builds_offline_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root ADK LlmAgent builds without network calls for both fallback and t00_example."""
    monkeypatch.setenv("SEED_OFFLINE_MODE", "1")
    agent_hello, pack_hello = build_agent("", enable_mcp_stdio=False)
    assert pack_hello.pack_id == "hello_fallback"
    assert agent_hello.name == "expedition_hello_fallback"

    agent_t00, pack_t00 = build_agent("t00_example", enable_mcp_stdio=False)
    assert pack_t00.pack_id == "t00_example"
    assert agent_t00.name == "expedition_t00_example"
    assert len(agent_t00.tools) == 3


def test_rejects_latest_model_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model configuration rejects forbidden -latest aliases."""
    monkeypatch.setenv("GEMINI_MODEL", "gemini-flash-latest")
    with pytest.raises(ValueError, match="-latest"):
        build_agent("t00_example", enable_mcp_stdio=False)


def test_t00_example_tools_execute_offline_and_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tools return valid JSON in offline mode and return structured errors on bad inputs."""
    monkeypatch.setenv("SEED_OFFLINE_MODE", "1")
    from seed_packs.t00_example.tools import (
        get_roastery_sales_stats,
        lookup_coffee_menu,
        submit_bean_reorder,
    )

    menu_res = json.loads(lookup_coffee_menu("espresso"))
    assert menu_res["status"] == "SUCCESS"
    assert menu_res["count"] == 2

    sales_res = json.loads(get_roastery_sales_stats("London Soho"))
    assert sales_res["status"] == "SUCCESS"
    assert sales_res["total_revenue_gbp"] == 4450.40

    order_res = json.loads(submit_bean_reorder("Ethiopian Yirgacheffe", 25, "express"))
    assert order_res["status"] == "SUCCESS"
    assert order_res["order"]["bags_kg"] == 25

    bad_order = json.loads(submit_bean_reorder("", -5, "standard"))
    assert bad_order["status"] == "INVALID_ARGUMENT"


def test_eval_loader_discovers_and_validates_five_golden_cases() -> None:
    """Evaluation loader discovers t00_example/eval/golden.test.json and validates all 5 cases."""
    files = discover_eval_files("t00_example")
    assert len(files) == 1
    assert files[0].name == "golden.test.json"

    eval_set, cases = load_eval_cases(files[0])
    assert eval_set is not None
    assert len(cases) == 5
    for case in cases:
        assert len(case.turns) >= 1
        assert case.turns[0].user_text
        assert len(case.turns[0].expected_tools) >= 1

    assert score_tool_trajectory(
        [{"name": "lookup_coffee_menu"}],
        [{"name": "lookup_coffee_menu", "args": {"category": "espresso"}}],
    ) == 1.0
    assert score_response_match("Flat White is £3.80", "We offer Flat White for £3.80.") > 0.4


# ---------------------------------------------------------------------------
# Red-team regression tests: public MCP contract, config placeholders, eval
# discovery, actionable errors and deploy/delete safety.
# ---------------------------------------------------------------------------


def test_mcp_extraction_uses_public_mcp_tools_list_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loader reads the explicit MCP_TOOLS list and never FastMCP private internals."""
    from app import loader

    (tmp_path / "rt_public_mcp.py").write_text(
        "def ping() -> str:\n    return 'pong'\n\nMCP_TOOLS = [ping]\n", encoding="utf-8"
    )
    (tmp_path / "rt_private_mcp.py").write_text(
        "import types\n"
        "def hidden() -> str:\n    return 'x'\n"
        "_tool = types.SimpleNamespace(fn=hidden)\n"
        "mcp = types.SimpleNamespace(_tool_manager=types.SimpleNamespace(_tools={'hidden': _tool}))\n",
        encoding="utf-8",
    )
    (tmp_path / "rt_broken_mcp.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    public = loader._extract_mcp_module_functions("rt_public_mcp")
    assert [fn.__name__ for fn in public] == ["ping"]
    assert loader._extract_mcp_module_functions("rt_private_mcp") == []
    with pytest.raises(ImportError, match="failed to import"):
        loader._extract_mcp_module_functions("rt_broken_mcp")


def test_resolve_env_config_ignores_template_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Values copied verbatim from .env.example never leak into project/bucket config."""
    from app import loader

    (tmp_path / ".env").write_text(
        "GOOGLE_CLOUD_PROJECT=my-real-project\n"
        "STAGING_BUCKET=gs://your-gcp-project-id-hackathon-staging\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text(
        "STAGING_BUCKET=gs://your-gcp-project-id-hackathon-staging\nGEMINI_MODEL=model-from-example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "your-gcp-project-id")
    monkeypatch.delenv("STAGING_BUCKET", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    assert loader.resolve_env_config("GOOGLE_CLOUD_PROJECT") == "my-real-project"
    assert loader.resolve_env_config("STAGING_BUCKET", default="gs://fallback") == "gs://fallback"
    assert loader.resolve_env_config("GEMINI_MODEL") == "model-from-example"


def test_discover_eval_files_fails_loudly_for_pack_without_eval_dir(tmp_path: Path) -> None:
    """A pack without eval/ must not silently run the unrelated hello_fallback cases."""
    pack_dir = tmp_path / "t99_demo"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text("id: t99_demo\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="has no eval/ folder"):
        discover_eval_files("t99", seed_packs_dir=tmp_path)


def test_explain_gcp_error_gives_actionable_hints() -> None:
    """Common newcomer failures map to a concrete next command."""
    from app.preflight import explain_gcp_error, parse_reasoning_engine_name

    disabled = explain_gcp_error(
        RuntimeError("403 SERVICE_DISABLED: Vertex AI API has not been used in project p1"), "p1"
    )
    assert "gcloud services enable" in disabled and "--project=p1" in disabled
    adc = explain_gcp_error(type("DefaultCredentialsError", (Exception,), {})("no creds"), "p1")
    assert "application-default login" in adc
    dataset = explain_gcp_error(RuntimeError("404 Not found: Dataset p1:seed_t00"), "p1")
    assert "make seed" in dataset
    assert explain_gcp_error(RuntimeError("something unrelated"), "p1") == ""

    parts = parse_reasoning_engine_name("projects/p1/locations/europe-west1/reasoningEngines/42")
    assert parts == {"project": "p1", "location": "europe-west1", "id": "42"}
    with pytest.raises(ValueError, match="full Agent Runtime resource name"):
        parse_reasoning_engine_name("42")


class _FakeRemote:
    def __init__(self, name: str, log: list[tuple[str, str]]) -> None:
        self.resource_name = name
        self._log = log

    def delete(self, force: bool = False) -> None:
        self._log.append(("delete", self.resource_name))


class _FakeAgentEngines:
    def __init__(self, update_error: Exception | None = None) -> None:
        self.update_error = update_error
        self.calls: list[tuple[str, str]] = []

    def update(self, *, resource_name: str, **_: object) -> _FakeRemote:
        self.calls.append(("update", resource_name))
        if self.update_error is not None:
            raise self.update_error
        return _FakeRemote(resource_name, self.calls)

    def create(self, **_: object) -> _FakeRemote:
        self.calls.append(("create", ""))
        return _FakeRemote("projects/p1/locations/europe-west1/reasoningEngines/999", self.calls)

    def get(self, name: str) -> _FakeRemote:
        self.calls.append(("get", name))
        return _FakeRemote(name, self.calls)


_SAVED = "projects/p1/locations/europe-west1/reasoningEngines/123"


def _prepare_deploy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: _FakeAgentEngines) -> Path:
    import deploy.deploy as dep

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p1")
    monkeypatch.setenv("AGENT_RUNTIME_LOCATION", "europe-west1")
    monkeypatch.setenv("ENABLE_MCP_STDIO", "0")
    monkeypatch.setenv("AGENT_RUNTIME_DEPLOY", "1")
    monkeypatch.setattr(dep.vertexai, "init", lambda **_: None)
    monkeypatch.setattr(dep, "AdkApp", lambda **_: object())
    monkeypatch.setattr(dep, "agent_engines", fake)
    state = tmp_path / ".deployed_agent.json"
    state.write_text(json.dumps({"resource_name": _SAVED, "project_id": "p1"}), encoding="utf-8")
    return state


def test_deploy_update_failure_never_creates_a_second_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-404 update error must raise instead of orphaning the saved agent."""
    import deploy.deploy as dep

    fake = _FakeAgentEngines(update_error=RuntimeError("400 INVALID_ARGUMENT: bad requirements"))
    state = _prepare_deploy(monkeypatch, tmp_path, fake)
    with pytest.raises(RuntimeError, match="Nothing was created"):
        dep.deploy_agent(pack_id="t00_example", state_file=state, check_bucket=False)
    assert fake.calls == [("update", _SAVED)]
    assert json.loads(state.read_text(encoding="utf-8"))["resource_name"] == _SAVED


def test_deploy_recreates_only_when_saved_agent_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A 404 on update (agent deleted in console) creates a new agent and records it."""
    import deploy.deploy as dep

    fake = _FakeAgentEngines(update_error=RuntimeError("404 NOT_FOUND: reasoningEngine 123"))
    state = _prepare_deploy(monkeypatch, tmp_path, fake)
    name = dep.deploy_agent(pack_id="t00_example", state_file=state, check_bucket=False)
    assert [c[0] for c in fake.calls] == ["update", "create"]
    assert json.loads(state.read_text(encoding="utf-8"))["resource_name"] == name != _SAVED


def test_deploy_refuses_state_file_from_another_region(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Switching region/project in .env must not silently abandon the saved agent."""
    import deploy.deploy as dep

    fake = _FakeAgentEngines()
    state = _prepare_deploy(monkeypatch, tmp_path, fake)
    monkeypatch.setenv("AGENT_RUNTIME_LOCATION", "europe-west2")
    with pytest.raises(RuntimeError, match="make delete"):
        dep.deploy_agent(pack_id="t00_example", state_file=state, check_bucket=False)
    assert fake.calls == []


def test_delete_targets_one_resource_and_keeps_unrelated_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Deleting an explicit other resource keeps the saved one tracked; deleting the saved one clears it."""
    import deploy.delete as dele

    fake = _FakeAgentEngines()
    monkeypatch.setattr(dele.vertexai, "init", lambda **_: None)
    monkeypatch.setattr(dele, "agent_engines", fake)
    monkeypatch.delenv("AGENT_ENGINE_RESOURCE_NAME", raising=False)
    state = tmp_path / ".deployed_agent.json"
    state.write_text(json.dumps({"resource_name": _SAVED}), encoding="utf-8")

    other = "projects/p1/locations/europe-west1/reasoningEngines/777"
    dele.delete_remote_agent(other, state_file=state)
    assert ("delete", other) in fake.calls and state.exists()

    dele.delete_remote_agent(state_file=state)
    assert ("delete", _SAVED) in fake.calls and not state.exists()

    with pytest.raises(ValueError, match="full Agent Runtime resource name"):
        dele.delete_remote_agent("777", state_file=state)


# ---------------------------------------------------------------------------
# Agent Runtime service-agent IAM: deployed agents run as
# service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com.
# ---------------------------------------------------------------------------

_RE_MEMBER = "serviceAccount:service-123456789@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
_RE_NUMERIC = "projects/123456789/locations/europe-west1/reasoningEngines/42"


class _FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200) -> None:
        self._payload = payload or {}
        self.status_code = status_code

    def json(self) -> dict:
        return json.loads(json.dumps(self._payload))

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Client Error: Forbidden")


class _FakeIamSession:
    def __init__(self, policy: dict) -> None:
        self.policy = policy
        self.calls: list[tuple[str, str]] = []
        self.set_payloads: list[dict] = []

    def get(self, url: str, **_: object) -> _FakeResponse:
        self.calls.append(("GET", url))
        return _FakeResponse({"projectNumber": "123456789"})

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append(("POST", url))
        if url.endswith(":getIamPolicy"):
            return _FakeResponse(self.policy)
        raw_body = kwargs.get("json")
        body = raw_body if isinstance(raw_body, dict) else {}
        self.set_payloads.append(body)
        return _FakeResponse(body.get("policy", {}))


class _ForbiddenIamStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def raise_forbidden(self, project_id: str, resource_name: str) -> bool:
        self.calls.append((project_id, resource_name))
        raise RuntimeError("403 Client Error: Forbidden for url: ...:setIamPolicy")


def test_ensure_reasoning_engine_iam_adds_missing_roles_additively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing runtime roles are added; existing bindings, conditions, auditConfigs and etag are preserved."""
    import deploy.deploy as dep

    conditional = {
        "role": "roles/bigquery.dataEditor",
        "members": [_RE_MEMBER],
        "condition": {"title": "temp", "expression": "request.time < timestamp('2030-01-01T00:00:00Z')"},
    }
    audit_configs = [{"service": "allServices", "auditLogConfigs": [{"logType": "ADMIN_READ"}]}]
    policy = {
        "version": 1,
        "etag": "BwXyz123=",
        "auditConfigs": audit_configs,
        "bindings": [
            {"role": "roles/owner", "members": ["user:alice@example.com"]},
            {"role": "roles/bigquery.jobUser", "members": ["user:bob@example.com"]},
            {"role": "roles/aiplatform.user", "members": [_RE_MEMBER]},
            conditional,
        ],
    }
    session = _FakeIamSession(policy)
    monkeypatch.setattr(dep, "_authorized_session", lambda: session)

    assert dep.ensure_reasoning_engine_iam("p1", "projects/p1/locations/europe-west1/reasoningEngines/42")
    assert session.calls[0] == ("GET", "https://cloudresourcemanager.googleapis.com/v1/projects/p1")
    assert session.calls[-1] == ("POST", "https://cloudresourcemanager.googleapis.com/v1/projects/p1:setIamPolicy")
    assert len(session.set_payloads) == 1
    sent = session.set_payloads[0]["policy"]
    assert sent["etag"] == "BwXyz123="
    assert sent["version"] == 3  # conditional binding present -> v3 required
    assert sent["auditConfigs"] == audit_configs
    bindings = sent["bindings"]
    assert {"role": "roles/owner", "members": ["user:alice@example.com"]} in bindings
    assert conditional in bindings  # untouched, and does NOT count as the grant
    assert {"role": "roles/bigquery.jobUser", "members": ["user:bob@example.com", _RE_MEMBER]} in bindings
    for role in dep.REASONING_ENGINE_RUNTIME_ROLES:
        assert any(
            b["role"] == role and "condition" not in b and _RE_MEMBER in b["members"] for b in bindings
        ), role
    assert len(bindings) == 6  # 4 original + new unconditional dataEditor + storage.objectAdmin


def test_ensure_reasoning_engine_iam_is_noop_when_roles_present(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """All four roles already bound unconditionally: setIamPolicy is never called."""
    import deploy.deploy as dep

    policy = {
        "etag": "BwAbc=",
        "bindings": [{"role": role, "members": [_RE_MEMBER]} for role in dep.REASONING_ENGINE_RUNTIME_ROLES],
    }
    session = _FakeIamSession(policy)
    monkeypatch.setattr(dep, "_authorized_session", lambda: session)

    assert dep.ensure_reasoning_engine_iam("p1", _RE_NUMERIC) is True
    assert session.set_payloads == []
    # Numeric project in the resource name: no projectNumber lookup either.
    assert [c[0] for c in session.calls] == ["POST"]
    assert "Verified BigQuery/Vertex/Storage IAM roles" in capsys.readouterr().out


def test_deploy_survives_iam_permission_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 403 from the IAM helper prints a NOTE with the gcloud fix but never fails make deploy."""
    import deploy.deploy as dep

    fake = _FakeAgentEngines()
    state = _prepare_deploy(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(dep, "check_adc", lambda _project: None)
    monkeypatch.setattr(dep, "_assert_staging_bucket", lambda *_: None)
    stub = _ForbiddenIamStub()
    monkeypatch.setattr(dep, "ensure_reasoning_engine_iam", stub.raise_forbidden)

    name = dep.deploy_agent(pack_id="t00_example", state_file=state, check_bucket=True)
    assert name == _SAVED
    assert stub.calls == [("p1", _SAVED)]
    assert json.loads(state.read_text(encoding="utf-8"))["resource_name"] == _SAVED
    out = capsys.readouterr().out
    assert "[deploy] NOTE:" in out
    assert "gcloud projects add-iam-policy-binding p1" in out
    assert "roles/bigquery.dataEditor" in out and "roles/bigquery.jobUser" in out


def test_explain_gcp_error_bigquery_403_mentions_agent_runtime_service_agent() -> None:
    """A BigQuery 403 hint keeps the generic guidance and names the gcp-sa-aiplatform-re fix."""
    from app.preflight import explain_gcp_error

    hint = explain_gcp_error(
        RuntimeError(
            "403 POST https://bigquery.googleapis.com/bigquery/v2/projects/p1/jobs: Access Denied: "
            "Project p1: User does not have bigquery.jobs.create permission in project p1."
        ),
        "p1",
    )
    assert "Permission denied in project 'p1'" in hint
    assert "gcp-sa-aiplatform-re.iam.gserviceaccount.com" in hint
    assert "roles/bigquery.dataEditor" in hint and "roles/bigquery.jobUser" in hint
    table_hint = explain_gcp_error(
        RuntimeError("403 Access Denied: Table p1:seed_t00.menu: User does not have permission"),
        "p1",
    )
    assert "gcp-sa-aiplatform-re.iam.gserviceaccount.com" in table_hint
    generic = explain_gcp_error(RuntimeError("403 PERMISSION_DENIED: storage.objects.get"), "p1")
    assert "Permission denied" in generic and "gcp-sa-aiplatform-re" not in generic
