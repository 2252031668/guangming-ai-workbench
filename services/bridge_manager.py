from __future__ import annotations

import platform
import secrets
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from services.model_profiles import PROFILE_MODE_BRIDGE, normalize_text


class BridgeError(RuntimeError):
    pass


_BRIDGE_PROCESSES: dict[str, subprocess.Popen] = {}


@dataclass(slots=True)
class BridgeRuntime:
    profile_id: str
    port: int
    auth_token: str
    base_url: str
    config_path: Path
    binary_path: Path
    runtime_dir: Path


def bridge_root(repo_dir: Path) -> Path:
    return repo_dir / "instance" / "bridge"


def bridge_runtime_dir(repo_dir: Path, profile_id: str) -> Path:
    return bridge_root(repo_dir) / profile_id


def local_bridge_binary_path(repo_dir: Path) -> Path:
    binary_name = "moonbridge.exe" if platform.system().lower() == "windows" else "moonbridge"
    return bridge_root(repo_dir) / "bin" / binary_name


def bundled_bridge_binary_path(repo_dir: Path) -> Path | None:
    if platform.system().lower() != "windows":
        return None
    return repo_dir / "tools" / "moonbridge.exe"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def yaml_quote(value: Any) -> str:
    text = normalize_text(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _reasoning_levels(chat_config: dict[str, Any]) -> list[str]:
    level = normalize_text(chat_config.get("reasoning_level")).lower()
    if level in {"low", "medium", "high"}:
        base = ["low", "medium", "high"]
        return [item for item in base if base.index(item) <= base.index(level)] or ["high"]
    return ["low", "medium", "high"]


def generate_bridge_config_text(
    profile: dict[str, Any],
    *,
    port: int,
    auth_token: str,
    trace_enabled: bool = False,
) -> str:
    alias = normalize_text(profile.get("model")) or "bridge-model"
    upstream_base = normalize_text(profile.get("base_url")).rstrip("/")
    upstream_key = normalize_text(profile.get("api_key"))
    chat_config = profile.get("chat_request_config") or {}
    context_window = int(profile.get("context_window") or 0)
    max_output_tokens = int(profile.get("max_output_tokens") or 0)
    lines = [
        'mode: "Transform"',
        "log:",
        '  level: "info"',
        '  format: "text"',
    ]
    if trace_enabled:
        lines.extend(
            [
                "trace:",
                "  enabled: true",
            ]
        )
    lines.extend(
        [
            "server:",
            f'  addr: "127.0.0.1:{port}"',
            f"  auth_token: {yaml_quote(auth_token)}",
            "defaults:",
            f"  model: {yaml_quote(alias)}",
            "providers:",
            "  upstream:",
            f"    base_url: {yaml_quote(upstream_base)}",
            f"    api_key: {yaml_quote(upstream_key)}",
            '    protocol: "openai-chat"',
            '    user_agent: "guangming-ai-workbench/0.1"',
            "    offers:",
            f"      - model: {yaml_quote(alias)}",
            "models:",
            f"  {alias}:",
            f"    display_name: {yaml_quote(profile.get('name') or alias)}",
            f"    description: {yaml_quote(profile.get('note') or 'Managed by Guangming AI Workbench')}",
        ]
    )
    if context_window > 0:
        lines.append(f"    context_window: {context_window}")
    if max_output_tokens > 0:
        lines.append(f"    max_output_tokens: {max_output_tokens}")
    if bool(chat_config.get("thinking_enabled")):
        lines.extend(
            [
                "    extensions:",
                "      deepseek_v4:",
                "        enabled: true",
                "        config:",
                "          thinking_budget_tokens: 4096",
                f"          reasoning_effort: {yaml_quote(chat_config.get('reasoning_level') or 'high')}",
            ]
        )
    if normalize_text(chat_config.get("reasoning_level")).lower() not in {"", "none"}:
        lines.extend(
            [
                f"    default_reasoning_level: {yaml_quote(chat_config.get('reasoning_level') or 'high')}",
                "    supported_reasoning_levels:",
            ]
        )
        for effort in _reasoning_levels(chat_config):
            lines.extend(
                [
                    f'      - effort: "{effort}"',
                    f"        description: {yaml_quote(f'{effort} reasoning effort')}",
                ]
            )
    lines.extend(
        [
            "routes:",
            f"  {alias}:",
            f"    model: {yaml_quote(alias)}",
            '    provider: "upstream"',
        ]
    )
    return "\n".join(lines) + "\n"


def write_bridge_config(
    repo_dir: Path,
    profile: dict[str, Any],
    *,
    port: int,
    auth_token: str,
    trace_enabled: bool = False,
) -> Path:
    runtime_dir = bridge_runtime_dir(repo_dir, profile["id"])
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = runtime_dir / "config.yml"
    config_path.write_text(
        generate_bridge_config_text(profile, port=port, auth_token=auth_token, trace_enabled=trace_enabled),
        encoding="utf-8",
    )
    return config_path


def bridge_process_key(profile_id: str) -> str:
    return normalize_text(profile_id) or "unknown"


def stop_bridge(profile_id: str) -> None:
    key = bridge_process_key(profile_id)
    process = _BRIDGE_PROCESSES.pop(key, None)
    if not process:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def resolve_bridge_binary(repo_dir: Path) -> tuple[Path, str] | None:
    bundled = bundled_bridge_binary_path(repo_dir)
    if bundled and bundled.exists():
        return bundled, "bundled"
    local = local_bridge_binary_path(repo_dir)
    if local.exists():
        return local, "local"
    return None


def ensure_bridge_binary(repo_dir: Path) -> Path:
    resolved = resolve_bridge_binary(repo_dir)
    if resolved:
        return resolved[0]
    if platform.system().lower() == "windows":
        expected = repo_dir / "tools" / "moonbridge.exe"
        raise BridgeError(f"缺少 Moon Bridge 二进制：{expected}")
    raise BridgeError("当前平台暂未内置 Moon Bridge 二进制，请使用 Windows x64 版本或手动提供本地路由程序。")


def start_bridge(repo_dir: Path, profile: dict[str, Any], *, trace_enabled: bool = False) -> BridgeRuntime:
    if profile.get("mode") != PROFILE_MODE_BRIDGE:
        raise BridgeError("当前模型配置不需要本地路由。")
    stop_bridge(profile["id"])
    binary = ensure_bridge_binary(repo_dir)
    port = free_port()
    auth_token = secrets.token_urlsafe(18)
    config_path = write_bridge_config(repo_dir, profile, port=port, auth_token=auth_token, trace_enabled=trace_enabled)
    runtime_dir = bridge_runtime_dir(repo_dir, profile["id"])
    log_path = runtime_dir / "bridge.log"
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(binary), "-config", str(config_path)],
        cwd=str(runtime_dir),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    _BRIDGE_PROCESSES[bridge_process_key(profile["id"])] = process
    return BridgeRuntime(
        profile_id=profile["id"],
        port=port,
        auth_token=auth_token,
        base_url=f"http://127.0.0.1:{port}/v1",
        config_path=config_path,
        binary_path=binary,
        runtime_dir=runtime_dir,
    )


def bridge_status(repo_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
    if profile.get("mode") != PROFILE_MODE_BRIDGE:
        return {
            "required": False,
            "ready": True,
            "phase": "native",
            "percent": 100,
            "message": "原生 Responses",
            "source": "native",
            "error": "",
        }

    resolved = resolve_bridge_binary(repo_dir)
    if resolved:
        _, source = resolved
        message = "本地路由可用：使用仓库内置二进制" if source == "bundled" else "本地路由可用：使用本地覆盖二进制"
        return {
            "required": True,
            "ready": True,
            "phase": "ready",
            "percent": 100,
            "message": message,
            "source": source,
            "error": "",
            "updated_at": now_iso(),
        }

    if platform.system().lower() == "windows":
        expected = repo_dir / "tools" / "moonbridge.exe"
        message = f"缺少 Moon Bridge 二进制：{expected}"
    else:
        message = "当前平台暂未内置 Moon Bridge 二进制。"
    return {
        "required": True,
        "ready": False,
        "phase": "binary_missing",
        "percent": 0,
        "message": message,
        "source": "",
        "error": "binary_missing",
        "updated_at": now_iso(),
    }
