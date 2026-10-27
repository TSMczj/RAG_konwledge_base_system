"""配置模块。

设计要点
--------
1. **敏感信息（API Key）优先从「系统环境变量」读取**；缺失时从 Windows DPAPI
   加密的本机文件读取，再由设置页写入。Key 绝不写入 ``.env``、绝不进日志。
2. **非敏感参数**（模型名、检索参数、路径等）从项目根目录的 ``.env`` 读取，
   ``.env.example`` 是模板。
3. 配置缺失时**立即抛错并给出可照做的修复步骤**，而不是等到第一次调接口才失败。

用法
----
    from app.config import get_settings

    settings = get_settings()          # 首次调用时校验，失败即抛 ConfigError
    settings.llm_model                 # -> 'deepseek-v4-flash'
    settings.deepseek_api_key.get_secret_value()
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：本文件位于 <root>/app/config.py，因此上溯两级
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

ENV_FILE: Path = PROJECT_ROOT / ".env"

#: 必须提供的两个密钥（系统环境变量优先，缺失时读本机 DPAPI 存储）
REQUIRED_SECRETS: tuple[str, ...] = ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY")

#: 阿里云百炼嵌入接口的单次批量硬上限
EMBEDDING_BATCH_LIMIT: int = 10


class ConfigError(RuntimeError):
    """配置缺失或非法。

    继承自 ``RuntimeError``，便于在应用启动阶段直接暴露为致命错误。
    """


def _secret_setup_hint(name: str) -> str:
    """生成「如何配置该环境变量」的可照做提示。"""
    return (
        f"缺少必需的 API Key：{name}。\n"
        f"\n"
        f"配置方式（任选其一）：\n"
        f"  1. 启动应用后，在首次引导或设置页保存 Key；\n"
        f"     会使用 Windows DPAPI 加密写入 data/secrets.dat。\n"
        f"  2. 配置操作系统环境变量（已有环境变量优先）。\n"
        f"\n"
        f"环境变量配置方法（Windows）：\n"
        f"  永久生效（需重开终端 / IDE 才会生效）：\n"
        f"      setx {name} \"你的key\"\n"
        f"  仅当前会话临时生效：\n"
        f"      CMD        :  set {name}=你的key\n"
        f"      PowerShell :  $env:{name}=\"你的key\"\n"
        f"      Git Bash   :  export {name}=\"你的key\"\n"
        f"\n"
        f"检查是否配置成功：\n"
        f"      Windows  :  echo %{name}%\n"
        f"      PowerShell:  $env:{name}\n"
    )


def _require_env(name: str) -> str:
    """读取必需的环境变量；缺失或为空则抛出带修复指引的 :class:`ConfigError`。"""
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ConfigError(_secret_setup_hint(name))
    return value.strip()


class Settings(BaseSettings):
    """非敏感配置 + 两个密钥。

    密钥字段声明在这里，是为了让它们随 ``Settings`` 一起被访问；
    校验顺序上，:func:`get_settings` 会先做一次友好检查，再交给 pydantic。
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 密钥（来自系统环境变量，见 REQUIRED_SECRETS）----------
    deepseek_api_key: SecretStr = Field(..., description="DeepSeek API Key")
    dashscope_api_key: SecretStr = Field(..., description="阿里云百炼 API Key")

    # ---------- API 端点（来自 .env）----------
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        description="DeepSeek 的 OpenAI 兼容端点",
    )
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="阿里云百炼的 OpenAI 兼容端点（新加坡地域需替换成专属域名）",
    )

    # ---------- 模型参数 ----------
    llm_model: str = Field(default="deepseek-v4-flash", description="生成模型")
    embedding_model: str = Field(default="text-embedding-v4", description="嵌入模型")
    embedding_dim: int = Field(
        default=1024,
        description="向量维度。⚠️ 一经确定不要更改，否则历史向量全部失效，必须整库重建",
    )
    embedding_batch_size: int = Field(
        default=EMBEDDING_BATCH_LIMIT,
        description="嵌入接口单次批量大小（百炼上限 10）",
    )

    # ---------- 检索与生成参数 ----------
    top_k: int = Field(default=5, description="检索返回条数")
    min_score: float = Field(
        default=0.25,
        description="最低相似度阈值（0~1）；低于该值的片段不进入上下文，避免无关内容污染回答",
    )
    max_context_tokens: int = Field(default=6000, description="拼进提示词的上下文上限")
    temperature: float = Field(default=0.2, description="生成温度，RAG 场景建议 0.0~0.3")
    max_history_turns: int = Field(default=5, description="多轮对话保留轮数")

    # ---------- 分块参数 ----------
    chunk_size: int = Field(default=500, description="分块大小（字符）")
    chunk_overlap: int = Field(default=80, description="块间重叠（字符）")

    # ---------- 存储路径（相对路径会被解析到项目根目录下）----------
    chroma_dir: Path = Field(default=Path("./data/chroma"), description="向量库持久化目录")
    upload_dir: Path = Field(default=Path("./data/uploads"), description="原始文档目录")
    collection_name: str = Field(default="knowledge_base", description="Chroma collection 名")

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    @field_validator("embedding_batch_size")
    @classmethod
    def _check_batch_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError("EMBEDDING_BATCH_SIZE 必须 >= 1")
        if v > EMBEDDING_BATCH_LIMIT:
            raise ValueError(
                f"EMBEDDING_BATCH_SIZE 不能超过 {EMBEDDING_BATCH_LIMIT}（阿里云百炼嵌入接口的硬限制）"
            )
        return v

    @field_validator("embedding_dim")
    @classmethod
    def _check_embedding_dim(cls, v: int) -> int:
        if v < 1:
            raise ValueError("EMBEDDING_DIM 必须为正整数")
        return v

    @field_validator("temperature")
    @classmethod
    def _check_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("TEMPERATURE 必须在 0.0 ~ 2.0 之间")
        return v

    @field_validator("top_k")
    @classmethod
    def _check_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError("TOP_K 必须 >= 1")
        return v

    @field_validator("min_score")
    @classmethod
    def _check_min_score(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("MIN_SCORE 必须在 0.0 ~ 1.0 之间")
        return v

    @field_validator("chunk_size", "chunk_overlap", "max_context_tokens", "max_history_turns")
    @classmethod
    def _check_positive(cls, v: int, info) -> int:
        if v < 0:
            raise ValueError(f"{info.field_name.upper()} 不能为负数")
        return v

    @field_validator("chroma_dir", "upload_dir")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        """把相对路径解析到项目根目录，避免受运行时工作目录影响。"""
        return v if v.is_absolute() else (PROJECT_ROOT / v).resolve()

    @model_validator(mode="after")
    def _check_chunk(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP({self.chunk_overlap}) 必须小于 CHUNK_SIZE({self.chunk_size})"
            )
        return self

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    def ensure_directories(self) -> None:
        """确保运行期需要的目录存在。"""
        for path in (self.chroma_dir, self.upload_dir, PROJECT_ROOT / "logs"):
            path.mkdir(parents=True, exist_ok=True)

    def masked(self) -> dict[str, object]:
        """返回可安全写入日志的配置摘要（密钥只显示长度，不显示内容）。"""
        return {
            "llm_model": self.llm_model,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "embedding_batch_size": self.embedding_batch_size,
            "top_k": self.top_k,
            "min_score": self.min_score,
            "max_context_tokens": self.max_context_tokens,
            "temperature": self.temperature,
            "max_history_turns": self.max_history_turns,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "chroma_dir": str(self.chroma_dir),
            "upload_dir": str(self.upload_dir),
            "collection_name": self.collection_name,
            "deepseek_base_url": self.deepseek_base_url,
            "dashscope_base_url": self.dashscope_base_url,
            "deepseek_api_key": f"<已设置，{len(_secret_len(self.deepseek_api_key))} 字符>",
            "dashscope_api_key": f"<已设置，{len(_secret_len(self.dashscope_api_key))} 字符>",
        }


def _secret_len(secret: SecretStr) -> str:
    """取密钥长度（用于日志脱敏展示）。"""
    return secret.get_secret_value()


def _format_validation_error(exc: ValidationError) -> str:
    """把 pydantic 的校验错误整理成人能读的提示。"""
    lines = ["配置校验失败，请检查 .env（模板见 .env.example）：", ""]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(根)"
        env_name = loc.upper()
        lines.append(f"  • {env_name}: {err.get('msg')}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """加载并校验配置（进程内只执行一次）。

    先单独检查两个密钥，以便给出「怎么配置」的友好提示；
    其余参数交给 pydantic 做类型与范围校验。

    Raises:
        ConfigError: 密钥缺失，或参数取值非法。
    """
    # 1) 先加载本机 DPAPI 加密 Key（已有系统环境变量优先），再检查密钥
    from app.local_secrets import load_local_secrets_into_env

    load_local_secrets_into_env()
    missing = [name for name in REQUIRED_SECRETS if not (os.environ.get(name) or "").strip()]
    if missing:
        raise ConfigError("\n".join(_secret_setup_hint(name) for name in missing))

    # 2) 再让 pydantic 校验全部配置
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc


def reset_settings_cache() -> None:
    """清空配置缓存（供测试使用）。"""
    get_settings.cache_clear()


if __name__ == "__main__":  # pragma: no cover - 手工自检入口
    from pprint import pformat

    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"配置文件  : {ENV_FILE} (存在={ENV_FILE.exists()})")
    print(f"Python    : {sys.version.split()[0]}")
    try:
        cfg = get_settings()
    except ConfigError as err:
        print("\n[配置检查未通过]\n")
        print(err)
        raise SystemExit(1)
    print("\n[配置检查通过]")
    print(pformat(cfg.masked(), sort_dicts=False))
