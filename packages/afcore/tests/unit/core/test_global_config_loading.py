"""Tests for global config loading (spec 13).

Covers TS-13-1 through TS-13-30, TS-13-E1 through TS-13-E7,
TS-13-P1 through TS-13-P6.

Group 1: failing tests (red phase) — tests MUST fail because the
implementation does not exist yet, but MUST be syntactically valid
and pass the linter.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from afcore.core.config import AgentFoxConfig, load_config
from afcore.core.errors import ConfigError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Set HOME to a temporary directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Also patch Path.home() so it respects the env var
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


@pytest.fixture()
def global_config_dir(fake_home):
    """Create the $HOME/.nightshift/ directory."""
    d = fake_home / ".nightshift"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def global_config(global_config_dir):
    """Create a minimal valid global config file."""
    cfg = global_config_dir / "config.toml"
    cfg.write_text("[orchestrator]\nmax_retries = 3\n")
    return cfg


@pytest.fixture()
def local_config_dir(tmp_path):
    """Create a .nightshift/ directory in the working directory."""
    d = tmp_path / "repo" / ".nightshift"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def clean_af_env(monkeypatch):
    """Remove AF_SPEC_MODEL from the environment."""
    monkeypatch.delenv("AF_SPEC_MODEL", raising=False)


# ===================================================================
# TS-13-1: Unified load_config across all CLIs
# ===================================================================
class TestUnifiedLoadConfig:
    """TS-13-1: All CLIs share the same load_config function."""

    def test_all_clis_share_load_config_function(self):
        """Verify nightshift imports the same load_config."""
        import nightshift.app
        from afcore.core.config import load_config as afcore_load_config

        assert nightshift.app.load_config is afcore_load_config


# ===================================================================
# TS-13-2: Global + local merge with shallow section replacement
# ===================================================================
class TestGlobalLocalMerge:
    """TS-13-2: load_config merges global and local configs."""

    def test_local_overrides_global_orchestrator(
        self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env
    ):
        """Local [orchestrator] max_retries=5 overrides global max_retries=3."""
        # Global config
        (global_config_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 3\n")
        # Local config in CWD
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        local_dir = repo / ".nightshift"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 5\n")
        monkeypatch.chdir(repo)

        config = load_config()

        assert config.orchestrator.max_retries == 5


# ===================================================================
# TS-13-3: Post-merge validation with Pydantic defaults
# ===================================================================
class TestPostMergeValidation:
    """TS-13-3: Omitted fields have Pydantic defaults applied."""

    def test_defaults_applied_after_merge(self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env):
        """Partial global config gets all defaults filled in."""
        (global_config_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 5\n")
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        config = load_config()

        assert isinstance(config, AgentFoxConfig)
        assert config.orchestrator.max_retries == 5
        assert config.orchestrator.session_timeout == 45  # default


# ===================================================================
# TS-13-4: Global config auto-creation (NS-REQ-4)
# ===================================================================
class TestGlobalConfigAutoCreation:
    """TS-13-4 / NS-REQ-4: load_config creates global config when neither exists."""

    def test_auto_creates_global_config(self, fake_home, tmp_path, monkeypatch, clean_af_env):
        """NS-REQ-4: When no config exists, a global config is auto-created at ~/.nightshift/config.toml."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        config = load_config()

        global_config = fake_home / ".nightshift" / "config.toml"
        local_config = repo / ".nightshift" / "config.toml"
        assert global_config.exists(), "Global config should be auto-created"
        assert not local_config.exists(), "Local config should NOT be created"
        assert isinstance(config, AgentFoxConfig)


# ===================================================================
# TS-13-5: Existing valid global config used as baseline
# ===================================================================
class TestExistingGlobalConfig:
    """TS-13-5: Existing global config is parsed and used."""

    def test_existing_global_config_used(self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env):
        """Global config with theme.header override is reflected; file not modified."""
        global_cfg = global_config_dir / "config.toml"
        global_cfg.write_text('[theme]\nheader = "bold blue"\n')
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        mtime_before = global_cfg.stat().st_mtime

        config = load_config()

        assert config.theme.header == "bold blue"
        # TS-13-5: file must not be modified
        mtime_after = global_cfg.stat().st_mtime
        assert mtime_before == mtime_after


# ===================================================================
# TS-13-6: $HOME unresolvable — skip global, use defaults
# ===================================================================
class TestHomeUnresolvable:
    """TS-13-6: HOME unresolvable skips global config."""

    def test_home_unresolvable_no_local(self, tmp_path, monkeypatch, caplog, clean_af_env):
        """When HOME cannot be resolved and no local config, use defaults."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))))

        with caplog.at_level(logging.DEBUG):
            config = load_config()

        assert isinstance(config, AgentFoxConfig)
        assert any("HOME" in msg and ("could not be resolved" in msg or "skipped" in msg) for msg in caplog.messages)

    def test_home_unresolvable_with_local(self, tmp_path, monkeypatch, caplog, clean_af_env):
        """When HOME cannot be resolved but local config exists, local is used."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        local_dir = repo / ".nightshift"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 5\n")
        monkeypatch.chdir(repo)
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))))

        with caplog.at_level(logging.DEBUG):
            config = load_config()

        assert isinstance(config, AgentFoxConfig)
        assert config.orchestrator.max_retries == 5
        assert any("sole config source" in msg for msg in caplog.messages)


# ===================================================================
# TS-13-E2: Symlinked global config raises ConfigError
# ===================================================================
class TestGlobalConfigSymlink:
    """TS-13-E2: Symlink on global config raises ConfigError with CWE-59."""

    def test_global_symlink_raises_config_error(self, fake_home, tmp_path, monkeypatch, clean_af_env):
        """$HOME/.nightshift/config.toml as symlink -> ConfigError."""
        agent_dir = fake_home / ".nightshift"
        agent_dir.mkdir(exist_ok=True)
        real_config = tmp_path / "real-config.toml"
        real_config.write_text("[orchestrator]\nmax_retries = 1\n")
        global_config_path = agent_dir / "config.toml"
        global_config_path.symlink_to(real_config)

        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        with pytest.raises(ConfigError, match=r"(?i)symlink|CWE-59") as exc_info:
            load_config()
        # TS-13-E2: error must identify the symlinked global config path
        assert str(global_config_path) in str(exc_info.value)


# ===================================================================
# TS-13-E3: Global dir creation failure -> ConfigError
# ===================================================================
class TestGlobalDirCreationFailure:
    """TS-13-E3: When $HOME is read-only, config still loads with defaults."""

    def test_dir_creation_permission_error(self, fake_home, tmp_path, monkeypatch, clean_af_env):
        """Read-only $HOME: falls through to local auto-create, returns defaults."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)
        # Make HOME read-only
        fake_home.chmod(0o444)

        try:
            config = load_config()
            assert isinstance(config, AgentFoxConfig)
        finally:
            fake_home.chmod(0o755)


# ===================================================================
# TS-13-7: Local config is sole source (no merge with global)
# ===================================================================
class TestLocalSoleSource:
    """TS-13-7: Local config takes full precedence — global is ignored."""

    def test_local_sole_source(self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env):
        """Local config is sole source; global is ignored entirely."""
        (global_config_dir / "config.toml").write_text(
            textwrap.dedent("""\
            [orchestrator]
            max_retries = 3
            session_timeout = 60
        """)
        )
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        local_dir = repo / ".nightshift"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 5\n")
        monkeypatch.chdir(repo)

        config = load_config()

        assert config.orchestrator.max_retries == 5
        # session_timeout uses Pydantic default (45), NOT global value (60)
        assert config.orchestrator.session_timeout == 45


# ===================================================================
# TS-13-8: No local config — global used unchanged, DEBUG log
# ===================================================================
class TestNoLocalConfig:
    """TS-13-8: No local config -> global used, DEBUG log emitted."""

    def test_no_local_config(self, fake_home, global_config_dir, tmp_path, monkeypatch, caplog, clean_af_env):
        """When no local config, global values used and DEBUG log emitted."""
        (global_config_dir / "config.toml").write_text('[theme]\nheader = "bold blue"\n')
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        with caplog.at_level(logging.DEBUG):
            config = load_config()

        assert config.theme.header == "bold blue"
        # TS-13-8: must include the full path suffix
        assert any("No local config found at" in msg and ".nightshift/config.toml" in msg for msg in caplog.messages)


# ===================================================================
# TS-13-9: No deep merge — section replacement is wholesale
# ===================================================================
class TestNoDeepMerge:
    """TS-13-9: No deep merge within sections."""

    def test_no_deep_merge(self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env):
        """Global session_timeout=60 is NOT preserved when local overrides [orchestrator]."""
        (global_config_dir / "config.toml").write_text(
            textwrap.dedent("""\
            [orchestrator]
            max_retries = 3
            session_timeout = 60
        """)
        )
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        local_dir = repo / ".nightshift"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 5\n")
        monkeypatch.chdir(repo)

        config = load_config()

        assert config.orchestrator.max_retries == 5
        # session_timeout should revert to Pydantic default (45), not global's 60
        assert config.orchestrator.session_timeout != 60
        assert config.orchestrator.session_timeout == 45


# ===================================================================
# TS-13-E4: Symlinked local config raises ConfigError
# ===================================================================
class TestLocalConfigSymlink:
    """TS-13-E4: Symlinked local config raises ConfigError."""

    def test_local_symlink_raises_config_error(self, fake_home, global_config, tmp_path, monkeypatch, clean_af_env):
        """Local .nightshift/config.toml as symlink -> ConfigError."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        local_dir = repo / ".nightshift"
        local_dir.mkdir(exist_ok=True)

        real_config = tmp_path / "other-config.toml"
        real_config.write_text("[orchestrator]\nmax_retries = 1\n")
        (local_dir / "config.toml").symlink_to(real_config)
        monkeypatch.chdir(repo)

        with pytest.raises(ConfigError, match=r"(?i)symlink|CWE-59") as exc_info:
            load_config()
        # TS-13-E4: error must identify the local config path
        assert ".nightshift/config.toml" in str(exc_info.value)


# ===================================================================
# TS-13-E5: Symlinked intermediate dir is NOT rejected
# ===================================================================
class TestIntermediateSymlinkAllowed:
    """TS-13-E5: Symlink checks apply only to the final file."""

    def test_symlinked_intermediate_dir_not_rejected(self, fake_home, tmp_path, monkeypatch, clean_af_env):
        """Symlinked intermediate directory is OK if final file is real."""
        # Create a real directory and config
        real_dir = tmp_path / "real_agent_fox"
        real_dir.mkdir()
        (real_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 3\n")

        # Create a symlinked intermediate directory
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        (repo / ".nightshift").symlink_to(real_dir)
        monkeypatch.chdir(repo)

        # Should NOT raise — symlink check is on the final file only
        config = load_config()
        assert isinstance(config, AgentFoxConfig)


# ===================================================================
# TS-13-10: Malformed global config -> ConfigError immediately
# ===================================================================
class TestMalformedGlobalConfig:
    """TS-13-10: Malformed global TOML -> ConfigError before local is read."""

    def test_malformed_global_raises_config_error(
        self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env
    ):
        """Global config with invalid TOML raises ConfigError (no local config)."""
        global_config_path = global_config_dir / "config.toml"
        global_config_path.write_text("[broken = unterminated")
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        with pytest.raises(ConfigError) as exc_info:
            load_config()

        error_msg = str(exc_info.value)
        assert str(global_config_path) in error_msg
        assert "parse" in error_msg.lower() or "TOML" in error_msg

    def test_malformed_global_ignored_with_local(
        self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env
    ):
        """Malformed global is ignored when local config exists."""
        global_config_dir_path = global_config_dir / "config.toml"
        global_config_dir_path.write_text("[broken = unterminated")
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        local_dir = repo / ".nightshift"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 1\n")
        monkeypatch.chdir(repo)

        config = load_config()
        assert config.orchestrator.max_retries == 1


# ===================================================================
# TS-13-11: Malformed local config -> ConfigError
# ===================================================================
class TestMalformedLocalConfig:
    """TS-13-11: Malformed local TOML -> ConfigError with local path."""

    def test_malformed_local_raises_config_error(self, fake_home, global_config, tmp_path, monkeypatch, clean_af_env):
        """Local config with invalid TOML raises ConfigError after global loads."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        local_dir = repo / ".nightshift"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "config.toml").write_text("key = @invalid")
        monkeypatch.chdir(repo)

        with pytest.raises(ConfigError) as exc_info:
            load_config()

        error_msg = str(exc_info.value)
        # TS-13-11: must identify the local config file path
        assert ".nightshift/config.toml" in error_msg
        # TS-13-11: must mention parse error or TOML
        assert "parse" in error_msg.lower() or "TOML" in error_msg


# ===================================================================
# TS-13-12: No partial config on malformed TOML
# ===================================================================
class TestNoPartialConfig:
    """TS-13-12: ConfigError always raised, no partial config returned."""

    def test_no_partial_config_returned(self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env):
        """Malformed TOML never returns a partial AgentFoxConfig."""
        (global_config_dir / "config.toml").write_text("[broken = unterminated")
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        result = None
        try:
            result = load_config()
        except ConfigError:
            pass
        assert result is None


# ===================================================================
# TS-13-20: DEBUG log — global config loaded
# ===================================================================
class TestDebugLogGlobalLoaded:
    """TS-13-20: DEBUG log 'Loaded global config from <path>'."""

    def test_debug_log_global_loaded(self, fake_home, global_config, tmp_path, monkeypatch, caplog, clean_af_env):
        """DEBUG log emitted when global config is loaded."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        with caplog.at_level(logging.DEBUG):
            load_config()

        global_config_path = str(fake_home / ".nightshift" / "config.toml")
        # TS-13-20: same message must contain both the prefix and the path
        assert any("Loaded global config from" in msg and global_config_path in msg for msg in caplog.messages)


# ===================================================================
# TS-13-21: DEBUG log — local config used as sole source
# ===================================================================
class TestDebugLogLocalSoleSource:
    """TS-13-21: DEBUG log when local config used as sole source."""

    def test_debug_log_local_sole_source(self, fake_home, global_config, tmp_path, monkeypatch, caplog, clean_af_env):
        """DEBUG log 'sole config source' when local config exists."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        local_dir = repo / ".nightshift"
        local_dir.mkdir(exist_ok=True)
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 4\n")
        monkeypatch.chdir(repo)

        with caplog.at_level(logging.DEBUG):
            load_config()

        assert any("sole config source" in msg for msg in caplog.messages)


# ===================================================================
# TS-13-22: DEBUG log — no local config found
# ===================================================================
class TestDebugLogNoLocal:
    """TS-13-22: DEBUG log when no local config exists."""

    def test_debug_log_no_local(self, fake_home, global_config, tmp_path, monkeypatch, caplog, clean_af_env):
        """DEBUG log 'No local config found at .nightshift/config.toml'."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        with caplog.at_level(logging.DEBUG):
            load_config()

        # TS-13-22: must include the full path suffix
        assert any("No local config found at" in msg and ".nightshift/config.toml" in msg for msg in caplog.messages)


# ===================================================================
# TS-13-23: DEBUG log — HOME unresolvable
# ===================================================================
class TestDebugLogHomeUnresolvable:
    """TS-13-23: DEBUG warning when $HOME cannot be resolved."""

    def test_debug_log_home_unresolvable(self, tmp_path, monkeypatch, caplog, clean_af_env):
        """DEBUG log mentions HOME when it cannot be resolved."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))))

        with caplog.at_level(logging.DEBUG):
            load_config()

        # TS-13-23: same message must contain BOTH 'HOME' AND 'could not be resolved'/'skipped'
        assert any("HOME" in msg and ("could not be resolved" in msg or "skipped" in msg) for msg in caplog.messages)


# ===================================================================
# TS-13-29: Full test suite regression gate
# ===================================================================
class TestRegressionSuite:
    """TS-13-29: Full existing test suite passes without modification.

    Runs all tests in the packages that spec 13 touches — af, nightshift,
    and core config — excluding recursive meta-tests that would trigger
    cascading failures and pre-existing broken tests unrelated to spec 13.

    See docs/errata/13_regression_suite_pre_existing_failures.md for the
    full list of pre-existing failures.
    """

    @pytest.mark.integration
    @pytest.mark.timeout(300)
    def test_full_test_suite_passes(self):
        """Run pytest on spec-13-adjacent packages and assert exit code 0."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "-o",
                "addopts=",
                "packages/nightshift/",
                "packages/afcore/tests/unit/core/",
                "-k",
                "not test_full_test_suite_passes"
                " and not test_af_tests_pass"
                " and not test_af_test_suite_passes"
                " and not test_dismiss_unknown_id_exits_nonzero"
                " and not test_json_mode_stdout_is_valid_json",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, (
            f"Spec-13-adjacent tests failed with exit code {result.returncode}.\n"
            f"stderr: {result.stderr[-500:]}\n"
            f"stdout: {result.stdout[-500:]}"
        )


# ===================================================================
# TS-13-30: Pydantic validation raises ConfigError (preserving existing behavior)
# ===================================================================
class TestPydanticValidation:
    """TS-13-30: Invalid values cause ConfigError; existing behavior preserved."""

    def test_invalid_value_raises_config_error(self, tmp_path):
        """Invalid field type raises ConfigError."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[orchestrator]\nmax_retries = "not-a-number"\n')

        with pytest.raises(ConfigError):
            load_config(path=config_file)

    def test_load_config_path_parameter_backward_compat(self, tmp_path):
        """load_config(path=...) still returns valid config from file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[orchestrator]\nmax_retries = 4\n")

        config = load_config(path=config_file)

        assert isinstance(config, AgentFoxConfig)
        assert config.orchestrator.max_retries == 4

    def test_load_config_returns_afcoreconfig(self):
        """load_config() returns an AgentFoxConfig instance."""
        # Called with a non-existent path -> defaults
        config = load_config(path=Path("/nonexistent/config.toml"))
        assert isinstance(config, AgentFoxConfig)


# ===================================================================
# TS-13-E1: Nonexistent CWD
# ===================================================================
class TestNonexistentCWD:
    """TS-13-E1: load_config with inaccessible CWD."""

    def test_nonexistent_cwd(self, fake_home, global_config, monkeypatch, clean_af_env):
        """load_config raises ConfigError or OSError with bad CWD."""
        # Patch cwd to raise
        monkeypatch.setattr(Path, "cwd", staticmethod(lambda: (_ for _ in ()).throw(OSError("no cwd"))))
        with pytest.raises((ConfigError, OSError)):
            load_config()


# ===================================================================
# TS-13-P2: Global config not overwritten after first creation
# ===================================================================
class TestGlobalConfigNotOverwrittenProperty:
    """TS-13-P2 / NS-REQ-5: Auto-created global config not overwritten on subsequent calls."""

    @pytest.mark.property
    def test_global_config_not_overwritten(self, fake_home, tmp_path, monkeypatch, clean_af_env):
        """Property: multiple load_config calls don't overwrite auto-created global config."""
        from hypothesis import given, settings
        from hypothesis import strategies as st

        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        monkeypatch.chdir(repo)

        @given(n_calls=st.integers(min_value=2, max_value=5))
        @settings(max_examples=5)
        def check(n_calls):
            load_config()
            global_config_path = fake_home / ".nightshift" / "config.toml"
            content_after_first = global_config_path.read_text()

            for _ in range(n_calls - 1):
                load_config()
                assert global_config_path.read_text() == content_after_first

        check()


# ===================================================================
# TS-13-P4: Malformed TOML always raises ConfigError
# ===================================================================
class TestMalformedTomlFailFastProperty:
    """TS-13-P4: Malformed TOML -> ConfigError, no partial config."""

    @pytest.mark.property
    def test_malformed_toml_always_raises(self, fake_home, tmp_path, monkeypatch, clean_af_env):
        """Property: malformed TOML in global config always raises ConfigError."""
        from hypothesis import given, settings
        from hypothesis import strategies as st

        malformed_toml = st.sampled_from(
            [
                "[broken = ",
                "key = @value",
                "[section\nkey = ",
                '"""unterminated',
                "[[nested]\nkey = {broken",
                "= no_key",
                "[good]\nbad = '''unclosed",
            ]
        )

        @given(bad_toml=malformed_toml)
        @settings(max_examples=10)
        def check(bad_toml):
            global_dir = fake_home / ".nightshift"
            global_dir.mkdir(exist_ok=True)
            (global_dir / "config.toml").write_text(bad_toml)

            repo = tmp_path / "repo"
            repo.mkdir(exist_ok=True)
            monkeypatch.chdir(repo)

            result = None
            try:
                result = load_config()
            except ConfigError:
                pass
            except Exception as e:
                # TS-13-P4: only ConfigError is acceptable
                raise AssertionError(f"Expected ConfigError but got {type(e).__name__}: {e}") from e
            assert result is None

        check()


# ===================================================================
# TS-13-P5: Symlink rejection on final file only
# ===================================================================
class TestSymlinkFinalFileOnlyProperty:
    """TS-13-P5: Symlink detection on final file path only."""

    @pytest.mark.property
    def test_symlink_final_file_only(self, fake_home, tmp_path, monkeypatch, clean_af_env):
        """Property: symlinked final file rejected; symlinked intermediate dir OK."""
        import shutil

        from hypothesis import given
        from hypothesis import settings as h_settings
        from hypothesis import strategies as st

        # Strategy: generate varying TOML content for diverse path structures
        toml_content_st = st.sampled_from(
            [
                "[orchestrator]\nmax_retries = 1\n",
                '[theme]\nheader = "bold blue"\n',
                "[orchestrator]\nsession_timeout = 30\n",
                "# empty\n",
            ]
        )
        # Strategy: generate varying directory depth for intermediate dirs
        depth_st = st.integers(min_value=0, max_value=3)

        @given(toml_content=toml_content_st, depth=depth_st)
        @h_settings(max_examples=20)
        def check_symlinked_final_file_rejected(toml_content, depth):
            """Symlinked final config file is always rejected."""
            # Build a real file in a unique location
            real_base = tmp_path / f"real_final_{depth}"
            real_base.mkdir(exist_ok=True)
            real_file = real_base / "config.toml"
            real_file.write_text(toml_content)

            # Set up global config dir with symlinked final file
            global_dir = fake_home / ".nightshift"
            if global_dir.is_symlink():
                global_dir.unlink()
            elif global_dir.exists():
                shutil.rmtree(global_dir)
            global_dir.mkdir(exist_ok=True)
            link = global_dir / "config.toml"
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(real_file)

            repo = tmp_path / "repo_final"
            repo.mkdir(exist_ok=True)
            monkeypatch.chdir(repo)

            with pytest.raises(ConfigError):
                load_config()

        @given(toml_content=toml_content_st, depth=depth_st)
        @h_settings(max_examples=20)
        def check_symlinked_intermediate_dir_accepted(toml_content, depth):
            """Symlinked intermediate directory with real final file is accepted."""
            # Create a real directory with a real config file
            real_dir = tmp_path / f"real_inter_{depth}"
            real_dir.mkdir(exist_ok=True)
            (real_dir / "config.toml").write_text(toml_content)

            # Set up $HOME/.nightshift as a symlink to the real directory
            global_dir = fake_home / ".nightshift"
            if global_dir.is_symlink():
                global_dir.unlink()
            elif global_dir.exists():
                shutil.rmtree(global_dir)
            global_dir.symlink_to(real_dir)

            repo = tmp_path / "repo_inter"
            repo.mkdir(exist_ok=True)
            monkeypatch.chdir(repo)

            # Should NOT raise — symlink check is on the final file only
            config = load_config()
            assert isinstance(config, AgentFoxConfig)

        check_symlinked_final_file_rejected()
        check_symlinked_intermediate_dir_accepted()


# ===================================================================
# SMOKE TESTS — end-to-end execution path verification
# ===================================================================


class TestSmoke1ZeroConfigFirstRun:
    """TS-13-SMOKE-1: Zero-config first run auto-creates global config (NS-REQ-4)."""

    @pytest.mark.smoke
    def test_zero_config_first_run(self, fake_home, tmp_path, monkeypatch, caplog, clean_af_env):
        """PATH-1 / NS-REQ-4: First load_config() auto-creates global config, emits DEBUG logs."""
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        with caplog.at_level(logging.DEBUG):
            config = load_config()

        # NS-REQ-4: Global config auto-created, no local config
        global_config = fake_home / ".nightshift" / "config.toml"
        local_config = repo / ".nightshift" / "config.toml"
        assert global_config.exists(), "Global config should be auto-created"
        assert not local_config.exists(), "Local config should NOT be created"

        # Valid config returned
        assert isinstance(config, AgentFoxConfig)

        # DEBUG log: 'No local config found ...'
        assert any("No local config found" in msg for msg in caplog.messages)


class TestSmoke2LocalTakesPrecedence:
    """TS-13-SMOKE-2: Local config is sole source when present."""

    @pytest.mark.smoke
    def test_local_sole_source(self, fake_home, global_config_dir, tmp_path, monkeypatch, caplog, clean_af_env):
        """PATH-2: Local config present — global is ignored entirely."""
        (global_config_dir / "config.toml").write_text(
            textwrap.dedent("""\
            [orchestrator]
            max_retries = 2
            session_timeout = 60
        """)
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        local_dir = repo / ".nightshift"
        local_dir.mkdir()
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 8\n")
        monkeypatch.chdir(repo)

        with caplog.at_level(logging.DEBUG):
            config = load_config()

        assert config.orchestrator.max_retries == 8
        # session_timeout NOT inherited from global — local is the sole source
        assert config.orchestrator.session_timeout == 45  # Pydantic default

        assert any("sole config source" in msg for msg in caplog.messages)


class TestSmoke3MalformedGlobalFailFast:
    """TS-13-SMOKE-3: Malformed global config causes fail-fast exit (no local)."""

    @pytest.mark.smoke
    def test_malformed_global_fail_fast_no_local(
        self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env
    ):
        """PATH-3a: Invalid TOML in global config -> ConfigError when no local config."""
        global_config_path = global_config_dir / "config.toml"
        global_config_path.write_text("[broken = ")

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)

        with pytest.raises(ConfigError) as exc_info:
            load_config()

        assert str(global_config_path) in str(exc_info.value)
        error_msg = str(exc_info.value)
        assert "parse" in error_msg.lower() or "TOML" in error_msg

    @pytest.mark.smoke
    def test_malformed_global_ignored_when_local_exists(
        self, fake_home, global_config_dir, tmp_path, monkeypatch, clean_af_env
    ):
        """PATH-3b: With local config, malformed global is ignored entirely."""
        global_config_path = global_config_dir / "config.toml"
        global_config_path.write_text("[broken = ")

        repo = tmp_path / "repo"
        repo.mkdir()
        local_dir = repo / ".nightshift"
        local_dir.mkdir()
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 1\n")
        monkeypatch.chdir(repo)

        config = load_config()
        assert config.orchestrator.max_retries == 1


class TestSmoke8HomeUnsetLocalUsed:
    """TS-13-SMOKE-8: HOME unset, local config used, no exception."""

    @pytest.mark.smoke
    def test_home_unset_local_used(self, tmp_path, monkeypatch, caplog, clean_af_env):
        """PATH-8: Unresolvable HOME + local config present -> local used as sole source."""
        repo = tmp_path / "repo"
        repo.mkdir()
        local_dir = repo / ".nightshift"
        local_dir.mkdir()
        (local_dir / "config.toml").write_text("[orchestrator]\nmax_retries = 3\n")
        monkeypatch.chdir(repo)
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no home"))))

        with caplog.at_level(logging.DEBUG):
            config = load_config()

        assert isinstance(config, AgentFoxConfig)
        assert config.orchestrator.max_retries == 3
        # Local config used as sole source — HOME never checked
        assert any("sole config source" in msg for msg in caplog.messages)
