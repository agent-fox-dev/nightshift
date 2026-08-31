"""Configuration system: TOML loading, pydantic models, defaults.

Loads project configuration from a TOML file, validates all fields using
pydantic models, and merges with documented defaults. Out-of-range numeric
values are clamped to the nearest valid bound rather than rejected.

The ``load_config()`` function is the single entry point. When called
without arguments it resolves a local config (``.nightshift/config.toml``)
with fallback to a global config (``$HOME/.nightshift/config.toml``).
Local config takes precedence over global config.
"""

from __future__ import annotations

import logging
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from agentfox.core.errors import ConfigError

logger = logging.getLogger(__name__)


def _clamp(
    value: int | float,
    *,
    ge: int | float | None = None,
    le: int | float | None = None,
    field_name: str,
) -> int | float:
    """Clamp a numeric value to valid bounds, logging a warning if adjusted."""
    original = value
    if ge is not None and value < ge:
        value = type(original)(ge) if isinstance(original, int) else ge
    if le is not None and value > le:
        value = type(original)(le) if isinstance(original, int) else le
    if value != original:
        logger.warning(
            "Config field '%s' value %s out of range, clamped to %s",
            field_name,
            original,
            value,
        )
    return value


class Clamped:
    """Annotation marking a numeric field for automatic clamping."""

    __slots__ = ("ge", "le", "cast")

    def __init__(
        self,
        ge: int | float | None = None,
        le: int | float | None = None,
        cast: type | None = None,
    ) -> None:
        self.ge = ge
        self.le = le
        self.cast = cast


def _auto_clamp_validator() -> Any:
    """Return a model_validator that clamps all fields annotated with Clamped."""

    @model_validator(mode="after")
    def _validate(self: Any) -> Any:
        for name, field_info in type(self).model_fields.items():
            for meta in field_info.metadata:
                if isinstance(meta, Clamped):
                    value = getattr(self, name)
                    if value is None:
                        continue
                    clamped = _clamp(value, ge=meta.ge, le=meta.le, field_name=name)
                    if meta.cast is not None:
                        clamped = meta.cast(clamped)
                    if clamped != value:
                        object.__setattr__(self, name, clamped)
        return self

    return _validate


class BackendConfig(BaseModel):
    """Backend provider configuration."""

    model_config = ConfigDict(extra="ignore")

    provider: Literal["claude", "deepagents", "google"] = Field(
        default="claude",
        description="Backend provider to use for agent sessions",
    )


class OrchestratorConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_retries: Annotated[int, Clamped(ge=0)] = Field(default=2, description="Maximum retries per task group")
    session_timeout: Annotated[int, Clamped(ge=1)] = Field(default=45, description="Session timeout in minutes")
    max_cost: float | None = Field(default=None, description="Maximum cost limit")
    max_sessions: int | None = Field(default=None, description="Maximum number of sessions")
    max_budget_usd: float = Field(
        default=20.0,
        ge=0.0,
        description="Maximum USD spend per session, 0 = unlimited",
    )

    _auto_clamp = _auto_clamp_validator()


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bash_allowlist: list[str] | None = Field(default=None, description="Allowed bash commands")
    bash_allowlist_extend: list[str] = Field(default_factory=list, description="Additional allowed bash commands")


class ThemeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    playful: bool = Field(default=True, description="Enable playful output style")
    header: str = Field(default="bold #ff8c00", description="Header text style")
    success: str = Field(default="bold green", description="Success text style")
    error: str = Field(default="bold red", description="Error text style")
    warning: str = Field(default="bold yellow", description="Warning text style")
    info: str = Field(default="#daa520", description="Info text style")
    tool: str = Field(default="bold #cd853f", description="Tool text style")
    muted: str = Field(default="dim", description="Muted text style")


class PlatformConfig(BaseModel):
    """Platform configuration for issue tracking.

    Requirements: 65-REQ-1.1, 65-REQ-1.2, 65-REQ-1.E1,
                  65-REQ-2.1, 65-REQ-2.2, 65-REQ-2.3, 65-REQ-2.E1
    """

    model_config = ConfigDict(extra="ignore")

    type: str = Field(default="none", description="Platform type (none, github, gitlab, or gitea)")
    url: str = Field(default="", description="Issue tracker URL (defaults from type; required for gitea)")


class KnowledgeProviderConfig(BaseModel):
    """Configuration for the pluggable knowledge provider.

    Requirements: 116-REQ-7.1, 116-REQ-7.2, 116-REQ-7.3, 116-REQ-7.E1
    """

    model_config = ConfigDict(extra="ignore")

    max_items: int = Field(default=10, description="Max total retrieval items")
    max_cross_group_items: int = Field(default=3, description="Max cross-group retrieval items")
    max_cross_spec_items: int = Field(default=3, description="Max cross-spec drift items")
    max_drift_age_days: int | None = Field(
        default=30,
        description="Max age in days for active drift findings; None disables age-based pruning",
    )
    max_summary_items: int = Field(
        default=5,
        description="Max session summaries from prior task groups injected as context",
    )


class KnowledgeConfig(BaseModel):
    """Knowledge store configuration.

    Requirements: 39-REQ-4.2, 114-REQ-8.1, 114-REQ-8.4, 114-REQ-8.5
    """

    model_config = ConfigDict(extra="ignore")

    store_path: str = Field(default=".nightshift/knowledge.duckdb", description="Path to knowledge store")
    provider: KnowledgeProviderConfig = Field(
        default_factory=KnowledgeProviderConfig,
        description="Pluggable knowledge provider configuration (gotcha TTL, retrieval caps, etc.)",
    )


class PerArchetypeConfig(BaseModel):
    """Unified per-archetype configuration table.

    Used via ``[archetypes.overrides.<name>]`` in config.toml. Provides a
    single, consolidated surface for all per-archetype knobs that previously
    required separate dict fields (``models``, ``max_turns``, ``thinking``,
    ``allowlists``).

    Requirements: 207-REQ-1, 207-REQ-2, 207-REQ-3
    """

    model_config = ConfigDict(extra="ignore")

    model_tier: str | None = Field(
        default=None,
        description="Model tier override (SIMPLE, STANDARD, ADVANCED). None = use registry default.",
    )
    model_variant: str | None = Field(
        default=None,
        description="Model variant override (fast, standard, extended). None = use registry default.",
    )
    max_turns: int | None = Field(
        default=None,
        description="Max turns override. 0 = unlimited. None = use registry default.",
        ge=0,
    )
    thinking_mode: Literal["adaptive", "disabled"] | None = Field(
        default=None,
        description="Extended thinking mode. None = use registry default.",
    )
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(
        default=None,
        description="Output effort level. Controls thinking depth and token spend. None = use registry default.",
    )
    allowlist: list[str] | None = Field(
        default=None,
        description="Bash command allowlist override. None = use registry default.",
    )
    max_budget_usd: float | None = Field(
        default=None,
        description=(
            "Per-archetype budget ceiling in USD. None = inherit global orchestrator.max_budget_usd. 0 = unlimited."
        ),
        ge=0.0,
    )
    compaction: bool | None = Field(
        default=None,
        description=(
            "Enable server-side context compaction to prevent context overflow in long sessions. "
            "None = use registry default (False)."
        ),
    )
    modes: dict[str, PerArchetypeConfig] = Field(
        default_factory=dict,
        description=(
            "Per-mode overrides for this archetype. TOML: [archetypes.overrides.<name>.modes.<mode>]. 97-REQ-3.1"
        ),
    )


# Required for self-referential Pydantic model (modes: dict[str, PerArchetypeConfig])
PerArchetypeConfig.model_rebuild()


class ArchetypesConfig(BaseModel):
    """Per-archetype configuration overrides."""

    model_config = ConfigDict(extra="ignore")

    overrides: dict[str, PerArchetypeConfig] = Field(
        default_factory=dict,
        description=("Unified per-archetype configuration tables. TOML: [archetypes.overrides.<name>]."),
    )


class ModelPricing(BaseModel):
    """Pricing for a single model.

    Requirements: 34-REQ-2.1, 34-REQ-2.E2
    """

    model_config = ConfigDict(extra="ignore")

    # Requirements: 34-REQ-2.E2
    input_price_per_m: Annotated[float, Clamped(ge=0.0)] = Field(
        default=0.0, description="USD per million input tokens"
    )
    output_price_per_m: Annotated[float, Clamped(ge=0.0)] = Field(
        default=0.0, description="USD per million output tokens"
    )
    cache_read_price_per_m: Annotated[float, Clamped(ge=0.0)] = Field(
        default=0.0, description="USD per million cache-read input tokens"
    )
    cache_creation_price_per_m: Annotated[float, Clamped(ge=0.0)] = Field(
        default=0.0, description="USD per million cache-creation input tokens"
    )

    _auto_clamp = _auto_clamp_validator()


def _default_pricing_models() -> dict[str, ModelPricing]:
    """Return default pricing for all known Claude models.

    Requirements: 34-REQ-2.2, 34-REQ-5.1
    """
    return {
        "claude-haiku-4-5": ModelPricing(
            input_price_per_m=1.00,
            output_price_per_m=5.00,
            cache_read_price_per_m=0.10,
            cache_creation_price_per_m=1.25,
        ),
        "claude-sonnet-4-6": ModelPricing(
            input_price_per_m=3.00,
            output_price_per_m=15.00,
            cache_read_price_per_m=0.30,
            cache_creation_price_per_m=3.75,
        ),
        "claude-opus-4-5": ModelPricing(
            input_price_per_m=5.00,
            output_price_per_m=25.00,
            cache_read_price_per_m=0.50,
            cache_creation_price_per_m=6.25,
        ),
        "claude-opus-4-6": ModelPricing(
            input_price_per_m=5.00,
            output_price_per_m=25.00,
            cache_read_price_per_m=0.50,
            cache_creation_price_per_m=6.25,
        ),
        # Rates retrieved from https://www.anthropic.com/pricing on 2026-06-29
        "claude-opus-4-6[1m]": ModelPricing(
            input_price_per_m=5.00,
            output_price_per_m=25.00,
            cache_read_price_per_m=0.50,
            cache_creation_price_per_m=6.25,
        ),
    }


class PricingConfig(BaseModel):
    """Per-model pricing configuration.

    Requirements: 34-REQ-2.1, 34-REQ-2.2, 34-REQ-2.E1
    """

    model_config = ConfigDict(extra="ignore")

    models: dict[str, ModelPricing] = Field(
        default_factory=_default_pricing_models,
        description="Per-model pricing configuration",
    )


class CachePolicy(StrEnum):
    """Prompt caching strategy for API calls.

    Applied to both auxiliary API calls (knowledge extraction, complexity
    assessment) via ``cached_messages_create()`` and main coding sessions
    via the Backend Protocol's ``cache_policy`` parameter.

    Requirements: 77-REQ-1.1, 77-REQ-1.3, 77-REQ-1.4, 77-REQ-1.5
    """

    NONE = "NONE"
    DEFAULT = "DEFAULT"
    EXTENDED = "EXTENDED"


class CachingConfig(BaseModel):
    """Prompt caching configuration.

    Requirements: 77-REQ-1.1, 77-REQ-1.2, 77-REQ-1.E1
    """

    model_config = ConfigDict(extra="ignore")

    cache_policy: CachePolicy = Field(
        default=CachePolicy.DEFAULT,
        description="Caching policy: NONE, DEFAULT (5-min), or EXTENDED (1-hour TTL)",
    )

    @field_validator("cache_policy", mode="before")
    @classmethod
    def _parse_policy_case_insensitive(cls, v: Any) -> Any:
        """Accept policy values case-insensitively."""
        if isinstance(v, str):
            return v.upper()
        return v


class NightShiftConfig(BaseModel):
    """Night-shift daemon configuration.

    Requirements: 61-REQ-9.1, 61-REQ-9.E1, 125-REQ-5.1, 125-REQ-5.3,
                  125-REQ-5.4
    """

    model_config = ConfigDict(extra="ignore")

    issue_check_interval: int = Field(
        default=900,
        description="Seconds between issue checks (minimum 60)",
    )

    pr_check_interval: int = Field(
        default=900,
        description="Seconds between PR status poll cycles (minimum 60)",
    )

    push_fix_branch: bool = Field(
        default=False,
        description="Push fix branches to origin before harvest",
    )

    max_parallel: int = Field(
        default=1,
        description="Maximum number of issues processed concurrently (1-8)",
    )

    max_pr_retries: int = Field(
        default=2,
        description="Maximum feedback iterations per PR before manual attention (0-10)",
    )

    @field_validator("issue_check_interval", "pr_check_interval")
    @classmethod
    def clamp_interval_minimum(cls, v: int, info: object) -> int:
        """Clamp intervals to a minimum of 60 seconds.

        Requirements: 61-REQ-9.E1, 07-REQ-1.1
        """
        if v < 60:
            logger.warning(
                "Config field '%s' value %d below minimum, clamped to 60",
                getattr(info, "field_name", "interval"),
                v,
            )
            return 60
        return v

    @field_validator("max_pr_retries")
    @classmethod
    def clamp_max_pr_retries(cls, v: int) -> int:
        """Clamp max_pr_retries to [0, 10].

        Requirements: 07-REQ-1.2
        """
        if v < 0:
            logger.warning(
                "Config field 'max_pr_retries' value %d below minimum, clamped to 0",
                v,
            )
            return 0
        if v > 10:
            logger.warning(
                "Config field 'max_pr_retries' value %d above maximum, clamped to 10",
                v,
            )
            return 10
        return v

    @field_validator("max_parallel")
    @classmethod
    def clamp_max_parallel(cls, v: int, info: object) -> int:
        """Clamp max_parallel to range [1, 8]."""
        if v < 1:
            logger.warning(
                "Config field '%s' value %d below minimum, clamped to 1",
                getattr(info, "field_name", "max_parallel"),
                v,
            )
            return 1
        if v > 8:
            logger.warning(
                "Config field '%s' value %d above maximum, clamped to 8",
                getattr(info, "field_name", "max_parallel"),
                v,
            )
            return 8
        return v


class WorkspaceConfig(BaseModel):
    """Workspace configuration."""

    model_config = ConfigDict(extra="ignore")

    integration_branch: str = Field(
        default="main",
        description="Git branch used as the integration target for all merges.",
    )
    merge_strategy: Literal["direct", "branch", "pr"] = Field(
        default="direct",
        description=(
            "Post-session branch integration: 'direct' (squash-merge), "
            "'branch' (keep locally), or 'pr' (open GitHub PR)."
        ),
    )


class AgentFoxConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    theme: ThemeConfig = Field(default_factory=ThemeConfig)
    platform: PlatformConfig = Field(default_factory=PlatformConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    archetypes: ArchetypesConfig = Field(default_factory=ArchetypesConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    caching: CachingConfig = Field(default_factory=CachingConfig)
    night_shift: NightShiftConfig = Field(default_factory=NightShiftConfig)

    _caching_explicit: bool = PrivateAttr(default=False)


def _check_symlink(path: Path) -> None:
    """Reject a config file path that is a symlink (CWE-59).

    Only the final file inode is checked — intermediate directories
    in the path are never checked for symlink status.

    Requirements: 13-REQ-2.E1, 13-REQ-3.E1, 13-REQ-3.E2
    """
    if path.is_symlink():
        raise ConfigError(
            f"Config file {path} is a symlink (CWE-59 violation). "
            "For security, config files must be regular files, not symlinks.",
            path=str(path),
        )


def _parse_toml_file(path: Path) -> dict:
    """Read and parse a TOML file, raising ConfigError on failure.

    Requirements: 13-REQ-4.1, 13-REQ-4.2, 13-REQ-4.3
    """
    raw = path.read_text(encoding="utf-8")
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"Failed to parse config file {path}: {exc}",
            path=str(path),
        ) from exc


def _validate_config_dict(data: dict, source: str = "<unknown>") -> AgentFoxConfig:
    """Validate a config dict through the AgentFoxConfig Pydantic model.

    Requirements: 13-REQ-1.3, 01-REQ-2.2
    """
    try:
        return AgentFoxConfig(**data)
    except ValidationError as exc:
        field_errors = []
        for err in exc.errors():
            loc = " → ".join(str(part) for part in err["loc"])
            msg = err["msg"]
            field_errors.append(f"  {loc}: {msg}")
        error_detail = "\n".join(field_errors)
        raise ConfigError(
            f"Invalid configuration in {source}:\n{error_detail}",
            path=source,
            details=exc.errors(),
        ) from exc


def load_config(path: Path | None = None) -> AgentFoxConfig:
    """Load config from TOML, validate, and merge with defaults.

    When called **without arguments** (``path is None``), resolves the
    global config from ``$HOME/.agent-fox/config.toml`` and the local
    config from ``.agent-fox/config.toml`` relative to the current
    working directory, merges them using shallow section replacement,
    validates through ``AgentFoxConfig``, and returns the result.

    When called **with a path**, loads and validates only that single
    file (backward compatibility with pre-spec-13 callers).

    Args:
        path: Path to a TOML configuration file.  If ``None``, use the
              global+local loading scheme.

    Returns:
        A fully populated AgentFoxConfig with defaults for missing fields.

    Raises:
        ConfigError: If a config file contains invalid TOML, fields with
                     wrong types, or the final config file path is a
                     symlink.

    Requirements: 13-REQ-1.1, 13-REQ-1.2, 13-REQ-1.3, 13-REQ-2.1,
                  13-REQ-2.2, 13-REQ-2.3, 13-REQ-2.E1, 13-REQ-2.E2,
                  13-REQ-3.1, 13-REQ-3.2, 13-REQ-3.3, 13-REQ-3.E1,
                  13-REQ-3.E2, 13-REQ-4.1, 13-REQ-4.2, 13-REQ-4.3,
                  13-REQ-5.1, 13-REQ-5.2, 13-REQ-7.1, 13-REQ-7.2,
                  13-REQ-7.3, 13-REQ-7.4
    """
    if path is not None:
        return _load_config_single_file(path)

    return _load_config_global_local()


def _load_config_single_file(path: Path) -> AgentFoxConfig:
    """Load config from a single explicit file path (backward compat).

    Preserves the pre-spec-13 behavior: missing file returns defaults,
    symlinked file raises ConfigError, invalid TOML raises ConfigError.

    Requirements: 01-REQ-2.E1, 01-REQ-2.E2
    """
    # 01-REQ-2.E1: missing file returns defaults without error
    if not path.exists():
        return AgentFoxConfig()

    # 13-REQ-2.E1 / 13-REQ-3.E1: symlink rejection
    _check_symlink(path)

    data = _parse_toml_file(path)

    # 01-REQ-2.6: log warning for unknown top-level keys
    known_sections = set(AgentFoxConfig.model_fields.keys())
    for key in data:
        if key not in known_sections:
            logger.warning("Ignoring unknown config section: '%s'", key)

    config = _validate_config_dict(data, source=str(path))
    if "caching" in data:
        config._caching_explicit = True
    return config


def _load_config_global_local() -> AgentFoxConfig:
    """Load config from local or global source.

    If a local config (``.nightshift/config.toml``) exists in CWD, it is
    used as the **sole** config source — the global config is not read.

    Otherwise, the global config (``~/.nightshift/config.toml``) is loaded.
    If neither exists, a minimal local config is auto-created for reference.
    """
    local_path = Path.cwd() / ".nightshift" / "config.toml"

    if local_path.exists():
        _check_symlink(local_path)
        local_dict = _parse_toml_file(local_path)

        logger.debug(
            "Local config found at %s — using as sole config source (global ignored)",
            local_path,
        )

        config = _validate_config_dict(local_dict, source=str(local_path))

        if "caching" in local_dict:
            config._caching_explicit = True

        return config

    logger.debug("No local config found at %s", local_path)

    global_dict: dict = {}
    home: Path | None = None

    try:
        home = Path.home()
    except (RuntimeError, OSError):
        logger.debug("$HOME could not be resolved; global config loading skipped")

    if home is not None:
        global_config_path = home / ".nightshift" / "config.toml"

        try:
            config_exists = global_config_path.exists()
        except OSError:
            config_exists = False

        if config_exists:
            _check_symlink(global_config_path)
            global_dict = _parse_toml_file(global_config_path)
            logger.debug("Loaded global config from %s", global_config_path)

    if not global_dict:
        _create_minimal_local_config(local_path)

    config = _validate_config_dict(global_dict, source="global config")

    if "caching" in global_dict:
        config._caching_explicit = True

    return config


def _create_minimal_local_config(path: Path) -> None:
    """Create a minimal local config with default values for reference."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        from agentfox.core.config_gen import generate_default_config

        path.write_text(generate_default_config(), encoding="utf-8")
        logger.info("Created minimal config at %s", path)
    except OSError:
        logger.debug("Could not create minimal config at %s", path)


def resolve_spec_root(config: AgentFoxConfig, project_root: Path) -> Path:
    """Resolve the spec root directory from config."""
    return project_root / ".nightshift" / "specs"
