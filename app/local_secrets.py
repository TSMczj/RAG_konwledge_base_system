"""本机 API Key 加密存储（Windows DPAPI）。

优先使用系统环境变量；没有环境变量时读取 ``data/secrets.dat`` 并用当前
Windows 用户身份解密，注入进程环境变量，供现有 config / 服务层复用。
文件内容永远不是明文 Key。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.context import PROVIDER_ENV_KEYS, mask_secret

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SECRET_FILE = PROJECT_ROOT / "data" / "secrets.dat"
SUPPORTED_PROVIDERS = tuple(PROVIDER_ENV_KEYS)
_LOCAL_LOADED: set[str] = set()


class SecretStoreError(RuntimeError):
    """本机密钥文件读取/DPAPI 操作失败。"""


if sys.platform == "win32":
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL


def _protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("本机加密存储仅支持 Windows DPAPI")
    buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "RAG local secrets",
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretStoreError(f"DPAPI 加密失败: {ctypes.WinError(ctypes.get_last_error())}")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def _unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("本机加密存储仅支持 Windows DPAPI")
    buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()
    description = wintypes.LPWSTR()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(description),
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretStoreError(f"DPAPI 解密失败: {ctypes.WinError(ctypes.get_last_error())}")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _kernel32.LocalFree(out_blob.pbData)
        if description:
            _kernel32.LocalFree(description)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalSecretStore:
    """DPAPI 加密的单用户密钥文件。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or os.environ.get("RAG_SECRETS_FILE") or DEFAULT_SECRET_FILE)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretStoreError(f"无法读取密钥文件: {exc}") from exc
        providers = payload.get("providers")
        return providers if isinstance(providers, dict) else {}

    def _write(self, providers: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps({"version": 1, "providers": providers}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def get(self, provider: str) -> str | None:
        item = self._read().get(provider)
        if not isinstance(item, dict) or not item.get("blob"):
            return None
        try:
            return _unprotect(base64.b64decode(item["blob"])).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecretStoreError(f"无法解码 {provider} Key: {exc}") from exc

    def set(self, provider: str, key: str) -> None:
        providers = self._read()
        providers[provider] = {
            "blob": base64.b64encode(_protect(key.encode("utf-8"))).decode("ascii"),
            "updated_at": _utc_now(),
        }
        self._write(providers)

    def delete(self, provider: str) -> None:
        providers = self._read()
        if provider in providers:
            providers.pop(provider)
            self._write(providers)

    def info(self, provider: str) -> dict[str, Any] | None:
        item = self._read().get(provider)
        return item if isinstance(item, dict) else None


def load_local_secrets_into_env() -> None:
    """把本机加密 Key 注入缺失的环境变量（已有环境变量优先）。"""
    store = LocalSecretStore()
    for provider, env_name in PROVIDER_ENV_KEYS.items():
        if (os.environ.get(env_name) or "").strip():
            continue
        value = store.get(provider)
        if value:
            os.environ[env_name] = value
            _LOCAL_LOADED.add(provider)


def set_local_secret(provider: str, key: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"未知的 provider: {provider}")
    LocalSecretStore().set(provider, key)
    os.environ[PROVIDER_ENV_KEYS[provider]] = key
    _LOCAL_LOADED.add(provider)


def clear_local_secret(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"未知的 provider: {provider}")
    LocalSecretStore().delete(provider)
    os.environ.pop(PROVIDER_ENV_KEYS[provider], None)
    _LOCAL_LOADED.discard(provider)


def secret_status(provider: str) -> dict[str, Any]:
    load_local_secrets_into_env()
    env_name = PROVIDER_ENV_KEYS.get(provider)
    if env_name is None:
        raise ValueError(f"未知的 provider: {provider}")
    value = (os.environ.get(env_name) or "").strip()
    local = LocalSecretStore().info(provider) if provider in _LOCAL_LOADED else None
    source = "local" if provider in _LOCAL_LOADED else ("env" if value else "none")
    return {
        "provider": provider,
        "configured": bool(value),
        "masked": mask_secret(value) if value else "",
        "source": source,
        "updated_at": local.get("updated_at") if local else None,
    }


__all__ = [
    "DEFAULT_SECRET_FILE",
    "LocalSecretStore",
    "SecretStoreError",
    "SUPPORTED_PROVIDERS",
    "clear_local_secret",
    "load_local_secrets_into_env",
    "secret_status",
    "set_local_secret",
]
