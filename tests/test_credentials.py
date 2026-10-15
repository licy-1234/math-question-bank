"""Deterministic tests for the model-service credential probe (no network)."""

from urllib import error as urlerror

from mathbank import credentials


def _route_env(**overrides):
    env = {
        "PREFER_SOLVE_MODEL": "DEEPSEEK/deepseek-flash",
        "PREFER_PARSE_MODEL": "SILICONFLOW/deepseek-ai/DeepSeek-V4-Flash",
        "PREFER_CLASSIFY_MODEL": "DEEPSEEK/deepseek-flash",
        "PREFER_DRAW_MODEL": "BAILIAN/qwen3.7-plus",
        "DEEPSEEK_API_KEY": "sk-secret-aaaa1234",
        "SILICONFLOW_API_KEY": "sk-secret-bbbb5678",
        "ALI_BAILIAN_API_KEY": "sk-secret-cccc9999",
    }
    env.update(overrides)
    return env


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_collect_probes_dedupes_shared_providers():
    probes = credentials.collect_credential_probes(_route_env())

    # 4 routes collapse onto 3 providers (SOLVE + CLASSIFY share DeepSeek).
    assert len(probes) == 3
    by_code = {probe.provider_code: probe for probe in probes}
    assert set(by_code) == {"deepseek", "siliconflow", "bailian"}
    assert by_code["deepseek"].routes == (
        "PREFER_SOLVE_MODEL",
        "PREFER_CLASSIFY_MODEL",
    )
    assert by_code["deepseek"].models_url == "https://api.deepseek.com/models"
    assert by_code["bailian"].api_key_env == "ALI_BAILIAN_API_KEY"


def test_probe_http_200_is_ok():
    probe = credentials.collect_credential_probes(_route_env())[0]
    result = credentials.probe_provider(
        probe, opener=lambda _req, _timeout: _FakeResponse(200)
    )

    assert result.outcome == credentials.OK
    assert result.status == 200


def test_probe_401_is_unauthorized_not_generic_error():
    probe = credentials.collect_credential_probes(_route_env())[0]

    def opener(request, _timeout):
        raise urlerror.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    result = credentials.probe_provider(probe, opener=opener)

    assert result.outcome == credentials.UNAUTHORIZED
    assert result.status == 401


def test_probe_timeout_is_unreachable_not_invalid_key():
    probe = credentials.collect_credential_probes(_route_env())[0]

    def opener(_request, _timeout):
        raise TimeoutError("timed out")

    result = credentials.probe_provider(probe, opener=opener)

    assert result.outcome == credentials.UNREACHABLE
    assert result.status is None
    assert "网络不可达" in result.detail


def test_probe_without_key_skips_network():
    env = _route_env(ALI_BAILIAN_API_KEY="")
    probe = next(
        item for item in credentials.collect_credential_probes(env)
        if item.provider_code == "bailian"
    )
    result = credentials.probe_provider(
        probe, opener=lambda _req, _timeout: _FakeResponse(200)
    )

    assert result.outcome == credentials.NO_KEY


def test_render_report_masks_secret_but_keeps_last_four():
    env = _route_env()
    probe = next(
        item for item in credentials.collect_credential_probes(env)
        if item.provider_code == "deepseek"
    )
    result = credentials.ProbeResult(
        probe, credentials.UNAUTHORIZED, 403, "密钥无效/未授权"
    )

    joined = "\n".join(credentials.render_report([result]))

    assert "aaaa1234" not in joined  # full secret never printed
    assert "1234" in joined  # last 4 kept for diagnosis
    assert "DEEPSEEK_API_KEY" in joined
    assert "PREFER_SOLVE_MODEL" in joined


def test_run_diagnostics_never_raises_when_router_breaks(monkeypatch):
    def boom(_config, _environ=None):
        raise RuntimeError("router exploded")

    monkeypatch.setattr(credentials, "resolve_text_provider", boom)

    lines = []
    results = credentials.run_credential_diagnostics(
        _route_env(), emit=lines.append
    )

    assert results == []
    assert lines and "跳过" in lines[0]


def test_run_diagnostics_emits_one_line_per_probe():
    lines = []
    results = credentials.run_credential_diagnostics(
        _route_env(), opener=lambda _req, _timeout: _FakeResponse(200), emit=lines.append
    )

    assert len(results) == 3
    assert len(lines) == 3
    assert all("✓" in line for line in lines)
