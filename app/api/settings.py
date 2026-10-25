"""设置页 API Key 接口（API 契约 P4 / T-5.8）。"""

from __future__ import annotations

import os
from time import perf_counter

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import reset_settings_cache
from app.local_secrets import (
    SUPPORTED_PROVIDERS,
    SecretStoreError,
    clear_local_secret,
    secret_status,
    set_local_secret,
)

router = APIRouter(prefix="/settings", tags=["settings"])

PROVIDER_MODELS = {
    "deepseek": "deepseek-v4-flash",
    "dashscope": "text-embedding-v4",
}
PROVIDER_BASE_URLS = {
    "deepseek": "DEEPSEEK_BASE_URL",
    "dashscope": "DASHSCOPE_BASE_URL",
}
DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}


class SecretPayload(BaseModel):
    key: str = Field(..., min_length=1, max_length=512)


def _check_provider(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"未知的 provider: {provider}"},
        )


def _providers() -> dict[str, dict]:
    return {provider: secret_status(provider) for provider in SUPPORTED_PROVIDERS}


@router.get("/secrets")
def get_secrets() -> dict:
    """返回两个 provider 的掩码状态；绝不返回完整 Key。"""
    providers = _providers()
    return {
        "providers": providers,
        "complete": all(item["configured"] for item in providers.values()),
    }


@router.put("/secrets/{provider}")
def put_secret(provider: str, payload: SecretPayload) -> dict:
    """保存/更换 Key。"""
    _check_provider(provider)
    key = payload.key.strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "message": "Key 不能为空"},
        )
    try:
        set_local_secret(provider, key)
        reset_settings_cache()
    except (SecretStoreError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL", "message": f"保存 Key 失败: {exc}"},
        ) from exc
    return secret_status(provider)


@router.delete("/secrets/{provider}")
def delete_secret(provider: str) -> dict:
    """清除本机存储的 Key。"""
    _check_provider(provider)
    try:
        clear_local_secret(provider)
        reset_settings_cache()
    except (SecretStoreError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL", "message": f"清除 Key 失败: {exc}"},
        ) from exc
    status = secret_status(provider)
    return {"provider": provider, "configured": status["configured"]}


@router.post("/secrets/{provider}/test")
def test_secret(provider: str) -> dict:
    """用已保存 Key 发起一次轻量上游连接测试。"""
    _check_provider(provider)
    status = secret_status(provider)
    if not status["configured"]:
        raise HTTPException(
            status_code=400,
            detail={"code": "CONFIG_MISSING", "message": f"{provider} 尚未配置 Key"},
        )

    env_name = "DEEPSEEK_API_KEY" if provider == "deepseek" else "DASHSCOPE_API_KEY"
    key = (os.environ.get(env_name) or "").strip()
    base = os.environ.get(PROVIDER_BASE_URLS[provider], DEFAULT_BASE_URLS[provider]).rstrip("/")
    started = perf_counter()
    try:
        response = httpx.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=8.0,
        )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "code": "UPSTREAM_TIMEOUT",
            "message": f"连接失败: {type(exc).__name__}",
            "retryable": True,
        }

    latency = round((perf_counter() - started) * 1000)
    ok = response.status_code == 200
    if ok:
        message = "连接正常"
    elif response.status_code in (401, 403):
        message = "Key 无效或没有访问权限"
    else:
        message = f"上游返回 HTTP {response.status_code}"
    return {
        "ok": ok,
        "latency_ms": latency,
        "model": PROVIDER_MODELS[provider],
        "message": message,
        "retryable": not ok,
    }


__all__ = ["router"]
