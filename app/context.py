"""请求上下文与密钥来源抽象（对应 ADR-001 / T-4.1）。

设计目标
--------
- 业务代码不再直接依赖全局 ``get_settings()`` 的 Key；
- 本地单用户使用 ``tenant_id="local"``，服务器模式从登录态注入 tenant；
- 密钥来源可替换：环境变量 → 本机加密存储 → 服务器密钥库；
- 接口只返回掩码状态，不返回完整 Key。

这是 Phase 0 的最小边界，不改变现有 CLI/索引行为。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from pydantic import SecretStr

#: provider 名 → 环境变量名
PROVIDER_ENV_KEYS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
}

#: 本地单用户的默认租户
LOCAL_TENANT = "local"


def mask_secret(value: str) -> str:
    """把 Key 显示为掩码。

    规则（与 API 契约一致）：

    - 以 ``sk-`` 开头：保留 ``sk-`` + 18 个 ``•`` + 末 4 位；
    - 其他格式：18 个 ``•`` + 末 4 位；
    - 长度 ≤ 8：全部显示为 ``••••``。
    """
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    prefix = "sk-" if key.startswith("sk-") else ""
    return f"{prefix}{'•' * 18}{key[-4:]}"


class SecretNotFound(RuntimeError):
    """请求的 provider 没有配置 Key。"""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"未配置 {provider} API Key")


@dataclass(frozen=True, slots=True)
class SecretStatus:
    """Key 的可展示状态；不包含明文。"""

    provider: str
    configured: bool
    masked: str = ""
    source: str = "none"


@runtime_checkable
class SecretProvider(Protocol):
    """密钥来源端口。

    本地实现是环境变量；后续可替换为 DPAPI/keyring 或服务器密钥库。
    """

    def get(self, provider: str, *, tenant_id: str = LOCAL_TENANT) -> SecretStr:
        """返回完整 Key，只允许在调用模型前短暂使用。"""
        ...

    def status(self, provider: str, *, tenant_id: str = LOCAL_TENANT) -> SecretStatus:
        """返回掩码状态，供设置页展示。"""
        ...


class EnvSecretProvider:
    """从系统环境变量读取 Key 的只读实现（当前本地模式使用）。"""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ

    def get(self, provider: str, *, tenant_id: str = LOCAL_TENANT) -> SecretStr:
        env_name = PROVIDER_ENV_KEYS.get(provider)
        if env_name is None:
            raise ValueError(f"未知的 provider: {provider}")
        value = str(self._env.get(env_name, "")).strip()
        if not value:
            raise SecretNotFound(provider)
        return SecretStr(value)

    def status(self, provider: str, *, tenant_id: str = LOCAL_TENANT) -> SecretStatus:
        env_name = PROVIDER_ENV_KEYS.get(provider, "")
        value = str(self._env.get(env_name, "")).strip() if env_name else ""
        return SecretStatus(
            provider=provider,
            configured=bool(value),
            masked=mask_secret(value),
            source="env" if value else "none",
        )


@dataclass(frozen=True, slots=True)
class RequestContext:
    """一次请求/一次 CLI 调用的上下文。

    ``tenant_id`` 只能由运行模式/认证层注入，不能从用户请求体读取。
    """

    tenant_id: str = LOCAL_TENANT
    user_id: str | None = None
    mode: str = "local"
    secrets: SecretProvider = field(default_factory=EnvSecretProvider)


def local_context(secrets: SecretProvider | None = None) -> RequestContext:
    """构造本地单用户上下文。"""
    return RequestContext(
        tenant_id=LOCAL_TENANT,
        user_id=None,
        mode="local",
        secrets=secrets or EnvSecretProvider(),
    )


__all__ = [
    "LOCAL_TENANT",
    "PROVIDER_ENV_KEYS",
    "EnvSecretProvider",
    "RequestContext",
    "SecretNotFound",
    "SecretProvider",
    "SecretStatus",
    "local_context",
    "mask_secret",
]
