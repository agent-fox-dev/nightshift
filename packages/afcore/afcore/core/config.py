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

from afcore.core.errors import ConfigError

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
    permission_mode: Literal["bypassPermissions", "acceptEdits", "plan", "default"] = Field(
        default="bypassPermissions",
        description=(
            "Claude Code permission mode. Root (UID 0) environments must use "
            "'acceptEdits' because Claude Code rejects 'bypassPermissions' "
            "when running as root."
        ),
    )


class ThemeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    header: str = Field(default="bold #ff8c00", description="Header text style")
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

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_variant_field(cls, data: Any) -> Any:
        """Reject the removed ``model_variant`` field with a clear error."""
        if isinstance(data, dict) and "model_variant" in data:
            raise ValueError(
                "The 'model_variant' field has been removed. "
                "Model selection now uses 'model_tier' only. "
                "Remove 'model_variant' from your config.toml."
            )
        return data

    model_tier: str | None = Field(
        default=None,
        description="Model tier override (SIMPLE, STANDARD, ADVANCED). None = use registry default.",
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


class ModelsConfig(BaseModel):
    """Config-driven model registry and tier-default overrides.

    Allows users to register new model IDs and remap tier defaults without
    a package release.  Entries in ``registry`` and ``tier_defaults`` overlay
    the hardcoded :data:`MODEL_REGISTRY` and :data:`TIER_DEFAULTS` in
    ``afcore.core.models``.

    Usage in config.toml::

        [models.tier_defaults]
        ADVANCED = "claude-fable-5-1"

        [models.registry.claude-fable-5-1]
        tier = "ADVANCED"

    Requirements: 01-REQ-5.1
    """

    model_config = ConfigDict(extra="ignore")

    registry: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Additional model registry entries keyed by model ID. "
            "Each value is a {tier} table. "
            "TOML: [models.registry.<model-id>]"
        ),
    )
    tier_defaults: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Override the default model ID for a tier. "
            "Keys must be SIMPLE, STANDARD, or ADVANCED. "
            "Values must exist in the merged registry (hardcoded + registry above). "
            "TOML: [models.tier_defaults]"
        ),
    )

    @model_validator(mode="after")
    def _validate_and_coerce_registry(self) -> ModelsConfig:
        """Parse registry dicts into ModelEntryConfig objects and cross-validate tier_defaults."""
        from afcore.core.models import MODEL_REGISTRY, ModelEntryConfig, ModelTier

        # Parse and validate each registry entry
        parsed: dict[str, Any] = {}
        for mid, raw in self.registry.items():
            if isinstance(raw, dict):
                try:
                    parsed[mid] = ModelEntryConfig(**raw)
                except Exception as exc:
                    raise ConfigError(
                        f"Invalid [models.registry.{mid}]: {exc}",
                        model=mid,
                    ) from exc
            else:
                parsed[mid] = raw
        object.__setattr__(self, "registry", parsed)

        # Build effective model ID set: hardcoded + user entries
        effective_ids = set(MODEL_REGISTRY.keys()) | set(parsed.keys())

        for tier_name, mid in self.tier_defaults.items():
            try:
                ModelTier(tier_name)
            except ValueError:
                valid_tiers = [t.value for t in ModelTier]
                raise ConfigError(
                    f"Invalid tier '{tier_name}' in [models.tier_defaults]. Valid tiers: {valid_tiers}",
                ) from None
            if mid not in effective_ids:
                raise ConfigError(
                    f"[models.tier_defaults] {tier_name} = '{mid}' refers to an unknown model ID. "
                    f"Add it to [models.registry.{mid}] first.",
                    model=mid,
                )

        return self


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

    # Requirements: 61-REQ-9.E1, 07-REQ-1.1
    issue_check_interval: Annotated[int, Clamped(ge=60)] = Field(
        default=900,
        description="Seconds between issue checks (minimum 60)",
    )

    # Requirements: 07-REQ-1.1
    pr_check_interval: Annotated[int, Clamped(ge=60)] = Field(
        default=900,
        description="Seconds between PR status poll cycles (minimum 60)",
    )

    push_fix_branch: bool = Field(
        default=False,
        description="Push fix branches to origin before harvest",
    )

    max_parallel: Annotated[int, Clamped(ge=1, le=8)] = Field(
        default=1,
        description="Maximum number of issues processed concurrently (1-8)",
    )

    # Requirements: 07-REQ-1.2
    max_pr_retries: Annotated[int, Clamped(ge=0, le=10)] = Field(
        default=2,
        description="Maximum feedback iterations per PR before manual attention (0-10)",
    )

    _auto_clamp = _auto_clamp_validator()


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


class HubConfig(BaseModel):
    """Hub API configuration for carry-patch mode.

    Requirements: 02-REQ-1.1
    """

    model_config = ConfigDict(extra="ignore")

    endpoint_url: str = Field(default="", description="Hub API base URL")


class CarryPatchConfig(BaseModel):
    """Carry-patch mode configuration.

    Requirements: 02-REQ-1.2, 02-REQ-1.3, 02-REQ-1.4, 02-REQ-1.5,
                  02-REQ-1.7
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(default=False, description="Enable carry-patch mode")
    workspace: str = Field(default="", description="Hub workspace slug")
    check_interval: Annotated[int, Clamped(ge=60)] = Field(
        default=300, description="Seconds between conflict checks (>=60)"
    )
    auto_resolve: bool = Field(default=True, description="Auto-resolve detected conflicts")
    rebuild_timeout: Annotated[int, Clamped(ge=1)] = Field(
        default=600, description="Max seconds to wait for hub rebuild (>=1)"
    )
    rebuild_poll_interval: Annotated[int, Clamped(ge=2)] = Field(
        default=5, description="Seconds between rebuild poll checks (>=2)"
    )
    max_resolve_retries: Annotated[int, Clamped(ge=0, le=10)] = Field(
        default=2, description="Max automatic conflict-resolve retries (0-10)"
    )

    _auto_clamp = _auto_clamp_validator()


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
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    caching: CachingConfig = Field(default_factory=CachingConfig)
    night_shift: NightShiftConfig = Field(default_factory=NightShiftConfig)
    hub: HubConfig = Field(default_factory=HubConfig)
    carry_patch: CarryPatchConfig = Field(default_factory=CarryPatchConfig)

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
    local config from ``.nightshift/config.toml`` relative to the
    current working directory.  If a local config exists it is used as
    the sole source; otherwise falls back to
    ``$HOME/.nightshift/config.toml``.  Validates through
    ``AgentFoxConfig`` and returns the result.

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
    If neither exists, a global config is auto-created at
    ``~/.nightshift/config.toml`` (NS-REQ-4).
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

    # NS-REQ-4: When neither local nor global config exists, auto-create
    # a global config at ~/.nightshift/config.toml.
    if not global_dict and home is not None:
        _create_default_global_config(home / ".nightshift" / "config.toml")

    config = _validate_config_dict(global_dict, source="global config")

    if "caching" in global_dict:
        config._caching_explicit = True

    return config


def _create_default_global_config(path: Path) -> None:
    """Create a default global config at ``~/.nightshift/config.toml``.

    NS-REQ-4: When neither local nor global config exists, create a global
    config using the default template.  Failures are logged but do not abort.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        from afcore.core.config_gen import generate_default_config

        path.write_text(generate_default_config(), encoding="utf-8")
        logger.info("Created global config at %s", path)
    except OSError:
        logger.debug("Could not create global config at %s", path)


def resolve_spec_root(config: AgentFoxConfig, project_root: Path) -> Path:
    """Resolve the spec root directory from config."""
    return project_root / ".nightshift" / "specs"
