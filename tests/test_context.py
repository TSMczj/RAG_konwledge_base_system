"""RequestContext / SecretProvider 边界测试（ADR-001 Phase 0）。"""

from __future__ import annotations

import pytest

from app.context import (
    LOCAL_TENANT,
    EnvSecretProvider,
    SecretNotFound,
    local_context,
    mask_secret,
)


def test_mask_secret_matches_contract() -> None:
    assert mask_secret("sk-1234567890abcdefghijklmnop4f2a") == "sk-" + "•" * 18 + "4f2a"
    assert mask_secret("plain-key-1234") == "•" * 18 + "1234"
    assert mask_secret("short") == "••••"
    assert mask_secret("") == ""


def test_env_provider_reads_configured_secret() -> None:
    provider = EnvSecretProvider(
        env={"DEEPSEEK_API_KEY": "sk-test-deepseek-4f2a"}
    )

    assert provider.get("deepseek").get_secret_value() == "sk-test-deepseek-4f2a"
    status = provider.status("deepseek")
    assert status.configured is True
    assert status.masked.endswith("4f2a")
    assert status.source == "env"


def test_env_provider_missing_secret() -> None:
    provider = EnvSecretProvider(env={})
    assert provider.status("dashscope").configured is False
    with pytest.raises(SecretNotFound):
        provider.get("dashscope")


def test_env_provider_unknown_provider() -> None:
    provider = EnvSecretProvider(env={})
    with pytest.raises(ValueError):
        provider.get("unknown")


def test_local_context_uses_local_tenant() -> None:
    ctx = local_context()
    assert ctx.tenant_id == LOCAL_TENANT
    assert ctx.mode == "local"
    assert ctx.user_id is None
    assert ctx.secrets.status("deepseek").provider == "deepseek"
