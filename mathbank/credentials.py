"""Read-only credential probe for the currently routed text providers.

The startup panel wants to tell the operator whether each ``PREFER_*`` model
route's provider actually accepts its configured API key.  This module performs
that check with a single read-only ``GET /models`` per distinct provider:

    - it never mutates configuration or sends chat completions;
    - it uses a short timeout and never blocks service readiness (callers run it
      on a daemon thread);
    - it never logs a secret (keys are shown as last-4 + length only);
    - it never raises: every failure becomes a descriptive result.

The network call is injected (``opener``) so tests can stub 200 / 401 / timeout
without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Iterable, Mapping, Optional
from urllib import error as _urlerror
from urllib import request as _urlrequest

from mathbank.ai_providers import resolve_text_provider


# The four routing keys the settings UI writes back into .env.
ROUTE_ENV_NAMES = (
    "PREFER_SOLVE_MODEL",
    "PREFER_PARSE_MODEL",
    "PREFER_CLASSIFY_MODEL",
    "PREFER_DRAW_MODEL",
)

OK = "ok"
UNAUTHORIZED = "unauthorized"
ERROR = "error"
UNREACHABLE = "unreachable"
NO_KEY = "no_key"


@dataclass(frozen=True)
class ProviderProbe:
    """One de-duplicated provider endpoint to probe."""

    provider_code: str
    provider_label: str
    api_key_env: str
    api_key: str
    api_base: str
    models_url: str
    routes: tuple[str, ...]

    @property
    def key_tail(self) -> str:
        key = self.api_key or ""
        return key[-4:] if len(key) >= 4 else "?"


@dataclass(frozen=True)
class ProbeResult:
    """The outcome of probing one provider."""

    probe: ProviderProbe
    outcome: str
    status: Optional[int]
    detail: str


def mask_secret(value: Optional[str]) -> str:
    """Return a display-safe rendering of a secret (last 4 + length)."""

    text = value or ""
    if not text:
        return "(空)"
    tail = text[-4:] if len(text) >= 4 else text
    return f"…{tail}（长度 {len(text)}）"


def collect_credential_probes(
    environ: Optional[Mapping[str, str]] = None,
) -> list[ProviderProbe]:
    """Resolve each routing key and de-duplicate by provider endpoint/credential.

    Four routing keys frequently collapse onto fewer providers (e.g. both SOLVE
    and CLASSIFY may use DeepSeek), so probing is de-duplicated to avoid
    redundant requests.
    """

    environment = os.environ if environ is None else environ
    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []

    for name in ROUTE_ENV_NAMES:
        raw = str(environment.get(name, "") or "").strip()
        if not raw:
            continue
        provider = resolve_text_provider(raw, environment)
        api_base = str(provider.api_base or "").rstrip("/")
        identity = (provider.provider_code, api_base, provider.api_key or "")
        if identity not in grouped:
            grouped[identity] = {
                "provider_code": provider.provider_code,
                "provider_label": provider.provider_label,
                "api_key_env": provider.api_key_env,
                "api_key": provider.api_key or "",
                "api_base": api_base,
                "routes": [],
            }
            order.append(identity)
        grouped[identity]["routes"].append(name)

    probes: list[ProviderProbe] = []
    for identity in order:
        item = grouped[identity]
        models_url = f"{item['api_base']}/models" if item["api_base"] else ""
        probes.append(
            ProviderProbe(
                provider_code=item["provider_code"],
                provider_label=item["provider_label"],
                api_key_env=item["api_key_env"],
                api_key=item["api_key"],
                api_base=item["api_base"],
                models_url=models_url,
                routes=tuple(item["routes"]),
            )
        )
    return probes


def _urlopen(request, timeout):  # pragma: no cover - thin network shim
    return _urlrequest.urlopen(request, timeout=timeout)


def probe_provider(
    probe: ProviderProbe,
    *,
    timeout: float = 5.0,
    opener: Callable = _urlopen,
) -> ProbeResult:
    """Probe one provider's ``/models`` endpoint with a short timeout."""

    if not probe.api_key:
        return ProbeResult(probe, NO_KEY, None, "未配置密钥，跳过校验")
    if not probe.models_url:
        return ProbeResult(probe, ERROR, None, "无法解析模型服务地址")

    request = _urlrequest.Request(probe.models_url, method="GET")
    request.add_header("Authorization", f"Bearer {probe.api_key}")
    request.add_header("Accept", "application/json")

    try:
        with opener(request, timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
        status = int(status)
    except _urlerror.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if code in (401, 403):
            return ProbeResult(probe, UNAUTHORIZED, code, "密钥无效/未授权")
        return ProbeResult(probe, ERROR, code, f"服务返回 HTTP {code}")
    except (TimeoutError, _urlerror.URLError, OSError) as exc:
        return ProbeResult(
            probe, UNREACHABLE, None, f"无法校验（网络不可达：{type(exc).__name__}）"
        )
    except Exception as exc:  # noqa: BLE001 - never let the probe crash startup
        return ProbeResult(
            probe, UNREACHABLE, None, f"无法校验（{type(exc).__name__}）"
        )

    if status == 200:
        return ProbeResult(probe, OK, status, "路由可用")
    if status in (401, 403):
        return ProbeResult(probe, UNAUTHORIZED, status, "密钥无效/未授权")
    return ProbeResult(probe, ERROR, status, f"服务返回 HTTP {status}")


def render_report(results: Iterable[ProbeResult]) -> list[str]:
    """Render probe results as log-safe lines (no plaintext secrets)."""

    lines: list[str] = []
    for result in results:
        probe = result.probe
        label = f"{probe.provider_label} ({probe.api_key_env})"
        routes = "/".join(probe.routes) or "-"
        tail = f"密钥尾 {probe.key_tail}" if probe.api_key else "无密钥"
        if result.outcome == OK:
            lines.append(f"[Model Credentials] ✓ {label} {tail}：路由可用（{routes}）")
        elif result.outcome == UNAUTHORIZED:
            lines.append(
                f"[Model Credentials] ✗ {label} {tail}：{result.detail}"
                f"（HTTP {result.status}），{routes} 链路将不可用"
            )
        elif result.outcome == NO_KEY:
            lines.append(f"[Model Credentials] – {label}：未配置密钥，跳过校验（{routes}）")
        elif result.outcome == UNREACHABLE:
            lines.append(
                f"[Model Credentials] ? {label} {tail}：{result.detail}（{routes}）"
            )
        else:
            lines.append(
                f"[Model Credentials] ! {label} {tail}：{result.detail}，"
                f"请稍后重试（{routes}）"
            )
    return lines


def run_credential_diagnostics(
    environ: Optional[Mapping[str, str]] = None,
    *,
    timeout: float = 5.0,
    opener: Callable = _urlopen,
    emit: Callable[[str], None] = print,
) -> list[ProbeResult]:
    """Probe every routed provider and emit a report.  Never raises.

    Designed to run on a daemon thread so it cannot block service readiness.
    """

    results: list[ProbeResult] = []
    try:
        probes = collect_credential_probes(environ)
    except Exception as exc:  # noqa: BLE001
        emit(f"[Model Credentials] 跳过：无法解析模型路由（{type(exc).__name__}）")
        return results

    if not probes:
        emit("[Model Credentials] 未配置任何 PREFER_* 模型路由，跳过凭据校验")
        return results

    for probe in probes:
        try:
            results.append(probe_provider(probe, timeout=timeout, opener=opener))
        except Exception as exc:  # noqa: BLE001
            results.append(
                ProbeResult(probe, UNREACHABLE, None, f"无法校验（{type(exc).__name__}）")
            )

    try:
        for line in render_report(results):
            emit(line)
    except Exception:  # noqa: BLE001
        pass
    return results
