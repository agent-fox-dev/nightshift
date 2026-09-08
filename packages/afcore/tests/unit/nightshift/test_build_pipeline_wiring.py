"""Tests for FixPipeline carry-patch wiring consistency (issue #41).

Validates that both ``_process_fix`` and ``_check_open_prs`` produce
``FixPipeline`` instances with identical carry-patch wiring via the
shared ``_build_pipeline()`` helper, and that ``_integrate_fix`` raises
an error when carry-patch is enabled but ``hub_client`` is ``None``.

Requirements: NS-REQ-41 (issue #41, AC-1 through AC-4)
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from afcore.nightshift.engine import NightShiftEngine
from afcore.nightshift.fix_pipeline import FixPipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    carry_patch_enabled: bool = True,
    workspace_slug: str = "my-workspace",
    merge_strategy: str = "direct",
) -> MagicMock:
    """Build a MagicMock config with carry_patch fields set."""
    config = MagicMock()
    config.carry_patch = MagicMock()
    config.carry_patch.enabled = carry_patch_enabled
    config.carry_patch.workspace = workspace_slug
    config.carry_patch.rebuild_timeout = 600
    config.carry_patch.rebuild_poll_interval = 5
    config.workspace = MagicMock()
    config.workspace.merge_strategy = merge_strategy
    config.workspace.integration_branch = "main"
    config.night_shift = MagicMock()
    config.night_shift.push_fix_branch = False
    config.night_shift.max_parallel = 1
    config.night_shift.max_pr_retries = 3
    config.orchestrator = MagicMock()
    config.orchestrator.max_retries = 2
    config.orchestrator.max_cost = None
    config.orchestrator.max_sessions = None
    config.platform = MagicMock()
    config.platform.type = "github"
    # Gate config: prevent log_ungated_warning from importing at
    # construction time.
    config.gate = MagicMock()
    config.gate.command = "true"
    return config


def _make_engine(
    config: MagicMock | None = None,
    hub_client: object | None = None,
) -> NightShiftEngine:
    """Build a NightShiftEngine with mocked dependencies."""
    if config is None:
        config = _make_config()
    return NightShiftEngine(
        config=config,
        platform=MagicMock(),
        hub_client=hub_client,
        repo_root=Path("/tmp/fake-repo"),
    )


# ---------------------------------------------------------------------------
# AC-1 / AC-4: _build_pipeline wires hub_client and workspace_slug
# ---------------------------------------------------------------------------


class TestBuildPipelineWiring:
    """_build_pipeline returns a pipeline with carry-patch wiring.

    Requirements: NS-REQ-41 (AC-1, AC-4)
    """

    def test_build_pipeline_includes_hub_client(self) -> None:
        """Pipeline built by _build_pipeline has hub_client set."""
        mock_hub = MagicMock()
        engine = _make_engine(hub_client=mock_hub)
        pipeline = engine._build_pipeline()
        assert pipeline._hub_client is mock_hub

    def test_build_pipeline_includes_workspace_slug(self) -> None:
        """Pipeline built by _build_pipeline has workspace_slug set."""
        config = _make_config(workspace_slug="ws-slug-42")
        engine = _make_engine(config=config, hub_client=MagicMock())
        pipeline = engine._build_pipeline()
        assert pipeline._workspace_slug == "ws-slug-42"

    def test_build_pipeline_no_hub_client_passes_none(self) -> None:
        """Pipeline built without hub_client has hub_client=None."""
        engine = _make_engine(hub_client=None)
        pipeline = engine._build_pipeline()
        assert pipeline._hub_client is None

    def test_build_pipeline_no_carry_patch_config_empty_slug(self) -> None:
        """When carry_patch config is absent, workspace_slug is empty."""
        config = _make_config()
        del config.carry_patch  # Remove the attribute entirely
        # Need to make getattr return None for missing carry_patch
        config.configure_mock(**{"carry_patch": None})
        engine = _make_engine(config=config)
        pipeline = engine._build_pipeline()
        assert pipeline._workspace_slug == ""


class TestSingleConstructionSite:
    """Only one FixPipeline(...) call exists in engine.py.

    Requirements: NS-REQ-41 (AC-4)
    """

    def test_single_fixpipeline_construction_in_engine(self) -> None:
        """Verify engine.py contains exactly one FixPipeline( call."""
        import inspect
        import re

        import afcore.nightshift.engine as engine_module

        source = inspect.getsource(engine_module)
        # Count direct FixPipeline( constructions (not imports, not type hints).
        # The pattern is "FixPipeline(" preceded by "= " or "return ".
        calls = re.findall(r"(?:=\s*|return\s+)FixPipeline\(", source)
        assert len(calls) == 1, f"Expected exactly 1 FixPipeline(...) construction in engine.py, found {len(calls)}"


class TestCheckOpenPrsUsesSharedPipeline:
    """_check_open_prs constructs its pipeline via _build_pipeline.

    Requirements: NS-REQ-41 (AC-1)
    """

    async def test_check_open_prs_pipeline_has_hub_client(self) -> None:
        """Pipeline used by _check_open_prs has the engine's hub_client."""
        mock_hub = MagicMock()
        config = _make_config(workspace_slug="test-ws")
        engine = _make_engine(config=config, hub_client=mock_hub)

        # Mock platform to return one af:pr issue
        mock_issue = MagicMock()
        mock_issue.number = 99
        mock_issue.title = "Test PR issue"
        mock_issue.body = "test body"
        engine._platform.list_issues_by_label = AsyncMock(return_value=[mock_issue])

        # Capture the pipeline passed to process_pr_issue
        captured_pipeline: list[FixPipeline] = []

        async def capture_process_pr(issue, *, config, platform, pipeline):
            captured_pipeline.append(pipeline)

        with patch(
            "afcore.nightshift.engine.process_pr_issue",
            side_effect=capture_process_pr,
        ):
            await engine._check_open_prs()

        assert len(captured_pipeline) == 1
        assert captured_pipeline[0]._hub_client is mock_hub
        assert captured_pipeline[0]._workspace_slug == "test-ws"


# ---------------------------------------------------------------------------
# AC-3: _integrate_fix errors on enabled-but-unwired carry-patch
# ---------------------------------------------------------------------------


class TestIntegrateFixMissingHubClient:
    """_integrate_fix logs an error when carry-patch enabled but hub_client is None.

    Requirements: NS-REQ-41 (AC-3)
    """

    async def test_logs_error_when_carry_patch_enabled_hub_client_none(self, caplog: pytest.LogCaptureFixture) -> None:
        """An ERROR-level log naming hub_client is emitted."""
        config = _make_config(carry_patch_enabled=True)
        pipeline = FixPipeline(
            config=config,
            platform=MagicMock(),
            hub_client=None,
            workspace_slug="my-ws",
        )

        issue = MagicMock()
        issue.number = 42
        issue.title = "Test issue"
        spec = MagicMock()
        spec.branch_name = "fix/issue-42"
        spec.issue_number = 42
        workspace = MagicMock()
        workspace.path = Path("/tmp/fake")
        workspace.branch = "fix/issue-42"

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            caplog.at_level(logging.ERROR),
        ):
            result, changed = await pipeline._integrate_fix(issue, spec, workspace)

        assert result == "error"
        assert changed == []
        # Check that an ERROR log was emitted mentioning hub_client
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) >= 1
        assert any("hub_client" in r.message for r in error_records), (
            f"Expected an ERROR log mentioning 'hub_client', got: {[r.message for r in error_records]}"
        )

    async def test_does_not_call_carry_patch_register(self) -> None:
        """_carry_patch_register_and_rebuild is NOT called."""
        config = _make_config(carry_patch_enabled=True)
        pipeline = FixPipeline(
            config=config,
            platform=MagicMock(),
            hub_client=None,
            workspace_slug="my-ws",
        )

        issue = MagicMock()
        issue.number = 42
        issue.title = "Test issue"
        spec = MagicMock()
        spec.branch_name = "fix/issue-42"
        spec.issue_number = 42
        workspace = MagicMock()
        workspace.path = Path("/tmp/fake")
        workspace.branch = "fix/issue-42"

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(
                pipeline,
                "_carry_patch_register_and_rebuild",
                AsyncMock(),
            ) as mock_register,
        ):
            await pipeline._integrate_fix(issue, spec, workspace)

        mock_register.assert_not_called()

    async def test_does_not_fallthrough_to_merge_strategy(self) -> None:
        """When carry-patch enabled but hub_client None, the merge-strategy
        switch is NOT reached — the method returns 'error' before that."""
        config = _make_config(carry_patch_enabled=True, merge_strategy="pr")
        pipeline = FixPipeline(
            config=config,
            platform=MagicMock(),
            hub_client=None,
            workspace_slug="my-ws",
        )

        issue = MagicMock()
        issue.number = 42
        issue.title = "Test issue"
        spec = MagicMock()
        spec.branch_name = "fix/issue-42"
        spec.issue_number = 42
        workspace = MagicMock()
        workspace.path = Path("/tmp/fake")
        workspace.branch = "fix/issue-42"

        with patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()):
            result, _ = await pipeline._integrate_fix(issue, spec, workspace)

        # Must be "error", NOT "pr_created" or "branch_only" —
        # those would indicate the merge-strategy switch was reached.
        assert result == "error"

    async def test_carry_patch_disabled_does_not_error(self) -> None:
        """When carry_patch.enabled is False, no error is logged even without hub_client."""
        config = _make_config(carry_patch_enabled=False)
        pipeline = FixPipeline(
            config=config,
            platform=MagicMock(),
            hub_client=None,
        )

        issue = MagicMock()
        issue.number = 42
        issue.title = "Test issue"
        spec = MagicMock()
        spec.branch_name = "fix/issue-42"
        spec.issue_number = 42
        workspace = MagicMock()
        workspace.path = Path("/tmp/fake")
        workspace.branch = "fix/issue-42"

        with (
            patch.object(pipeline, "_auto_commit_pending_changes", AsyncMock()),
            patch.object(pipeline, "_harvest_and_push", AsyncMock(return_value=["file.py"])),
        ):
            result, changed = await pipeline._integrate_fix(issue, spec, workspace)

        # Should proceed to the normal direct-merge path
        assert result == "merged"
