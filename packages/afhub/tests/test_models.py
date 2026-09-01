"""Tests for Pydantic data models in afhub.models.

Covers: TS-01-31 through TS-01-36 (spec 01, group 5).
Requirements: 01-REQ-5 (01-REQ-5.1 through 01-REQ-5.6, edge cases E1-E4).

These tests are written against the stub implementation and will FAIL until
group 10 provides the real implementation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from afhub.models import (
    Patch,
    PatchDetail,
    PatchResult,
    PatchStatusDashboard,
    PatchSummary,
    RebuildJob,
    RebuildPreview,
    RebuildPreviewPatchResult,
    RerereEntry,
    SyncResult,
    Workspace,
)

# ---------------------------------------------------------------------------
# TS-01-31: All Pydantic models use model_config = ConfigDict(extra='ignore')
# ---------------------------------------------------------------------------

ALL_MODEL_CLASSES = [
    Workspace,
    Patch,
    RebuildJob,
    PatchResult,
    SyncResult,
    RerereEntry,
    PatchStatusDashboard,
    PatchDetail,
    PatchSummary,
    RebuildPreview,
    RebuildPreviewPatchResult,
]


class TestExtraIgnoreConfig:
    """TS-01-31 -- All Pydantic models in afhub.models use
    model_config = ConfigDict(extra='ignore') so unknown fields are silently
    discarded.

    Requirements: 01-REQ-5.1, 01-REQ-5.E2
    """

    @pytest.mark.parametrize("model_cls", ALL_MODEL_CLASSES, ids=lambda c: c.__name__)
    def test_model_config_extra_is_ignore(self, model_cls: type) -> None:
        """Each model class declares extra='ignore' in its model_config."""
        assert model_cls.model_config.get("extra") == "ignore"

    def test_workspace_extra_field_discarded(self) -> None:
        """Workspace silently discards unknown fields (01-REQ-5.E2)."""
        ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="ready",
            sync_status="ok",
            totally_unknown_field="ignored",
        )
        assert not hasattr(ws, "totally_unknown_field")

    def test_patch_detail_extra_field_discarded(self) -> None:
        """PatchDetail silently discards unknown fields (01-REQ-5.E2)."""
        pd = PatchDetail(
            id="p1",
            branch_name="feat/x",
            position=1,
            status="active",
            last_rebuild_result=None,
            unknown_future_field="value",
        )
        assert not hasattr(pd, "unknown_future_field")

    def test_rebuild_job_extra_field_discarded(self) -> None:
        """RebuildJob silently discards unknown fields (01-REQ-5.E2)."""
        job = RebuildJob(
            id="job-1",
            status="pending",
            created_at="2026-01-01T00:00:00Z",
            some_new_api_field=42,
        )
        assert not hasattr(job, "some_new_api_field")

    def test_patch_extra_field_discarded(self) -> None:
        """Patch silently discards unknown fields (01-REQ-5.E2)."""
        p = Patch(
            id="p1",
            workspace_slug="ws1",
            branch_name="feat/x",
            position=1,
            status="active",
            added_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            brand_new_field="surprise",
        )
        assert not hasattr(p, "brand_new_field")


# ---------------------------------------------------------------------------
# TS-01-32: Workspace model validates required fields and defaults optional
#           fields to None
# ---------------------------------------------------------------------------


class TestWorkspaceModel:
    """TS-01-32 -- Workspace model validates required fields (slug, git_url,
    workspace_mode, status, clone_status, sync_status) and defaults optional
    fields to None.

    Requirements: 01-REQ-5.2, 01-REQ-5.E1
    """

    def test_workspace_required_fields_parse(self) -> None:
        """Workspace accepts all required fields and returns a valid instance."""
        ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="ready",
            sync_status="ok",
        )
        assert ws.slug == "ws1"
        assert ws.git_url == "https://git.example.com/repo.git"
        assert ws.workspace_mode == "carry"
        assert ws.status == "active"
        assert ws.clone_status == "ready"
        assert ws.sync_status == "ok"

    def test_workspace_optional_fields_default_to_none(self) -> None:
        """All optional fields on Workspace default to None when not provided."""
        ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="ready",
            sync_status="ok",
        )
        assert ws.clone_error is None
        assert ws.sync_error is None
        assert ws.sync_mode is None
        assert ws.upstream_url is None
        assert ws.upstream_head_sha is None
        assert ws.head_sha is None
        assert ws.integration_branch is None
        assert ws.last_sync_at is None

    def test_workspace_optional_fields_accept_values(self) -> None:
        """Optional fields accept explicit values."""
        ws = Workspace(
            slug="ws1",
            git_url="https://git.example.com/repo.git",
            workspace_mode="carry",
            status="active",
            clone_status="ready",
            sync_status="ok",
            clone_error="disk full",
            upstream_url="https://upstream.example.com/repo.git",
            head_sha="abc123",
            integration_branch="integration/main",
            last_sync_at="2026-01-01T00:00:00Z",
        )
        assert ws.clone_error == "disk full"
        assert ws.upstream_url == "https://upstream.example.com/repo.git"
        assert ws.head_sha == "abc123"
        assert ws.integration_branch == "integration/main"
        assert ws.last_sync_at == "2026-01-01T00:00:00Z"

    def test_workspace_missing_required_field_raises_validation_error(self) -> None:
        """Omitting a required field (slug) raises pydantic.ValidationError (01-REQ-5.E1)."""
        with pytest.raises(ValidationError) as exc_info:
            Workspace(
                git_url="https://git.example.com/repo.git",
                workspace_mode="carry",
                status="active",
                clone_status="ready",
                sync_status="ok",
            )
        # The error should mention the missing field
        assert "slug" in str(exc_info.value)

    def test_workspace_missing_git_url_raises_validation_error(self) -> None:
        """Omitting git_url raises pydantic.ValidationError."""
        with pytest.raises(ValidationError):
            Workspace(
                slug="ws1",
                workspace_mode="carry",
                status="active",
                clone_status="ready",
                sync_status="ok",
            )

    def test_workspace_missing_all_required_raises_validation_error(self) -> None:
        """Constructing Workspace with no arguments raises pydantic.ValidationError."""
        with pytest.raises(ValidationError):
            Workspace()


# ---------------------------------------------------------------------------
# TS-01-33: Patch model validates required fields and defaults optional
#           fields to None
# ---------------------------------------------------------------------------


class TestPatchModel:
    """TS-01-33 -- Patch model validates required fields (id, workspace_slug,
    branch_name, position, status, added_at, updated_at) and defaults optional
    fields to None.

    Requirements: 01-REQ-5.3
    """

    def test_patch_required_fields_parse(self) -> None:
        """Patch accepts all required fields and returns a valid instance."""
        p = Patch(
            id="p1",
            workspace_slug="ws1",
            branch_name="feat/x",
            position=1,
            status="active",
            added_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert p.id == "p1"
        assert p.workspace_slug == "ws1"
        assert p.branch_name == "feat/x"
        assert p.position == 1
        assert p.status == "active"
        assert p.added_at == "2026-01-01T00:00:00Z"
        assert p.updated_at == "2026-01-01T00:00:00Z"

    def test_patch_optional_fields_default_to_none(self) -> None:
        """All optional fields on Patch default to None when not provided."""
        p = Patch(
            id="p1",
            workspace_slug="ws1",
            branch_name="feat/x",
            position=1,
            status="active",
            added_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert p.conflict_files is None
        assert p.upstream_pr_url is None
        assert p.description is None
        assert p.deleted_at is None

    def test_patch_optional_fields_accept_values(self) -> None:
        """Optional fields accept explicit values."""
        p = Patch(
            id="p1",
            workspace_slug="ws1",
            branch_name="feat/x",
            position=1,
            status="active",
            added_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            conflict_files=["src/foo.py"],
            upstream_pr_url="https://github.com/org/repo/pull/1",
            description="a patch",
            deleted_at="2026-06-01T00:00:00Z",
        )
        assert p.conflict_files == ["src/foo.py"]
        assert p.upstream_pr_url == "https://github.com/org/repo/pull/1"
        assert p.description == "a patch"
        assert p.deleted_at == "2026-06-01T00:00:00Z"

    def test_patch_missing_required_field_raises_validation_error(self) -> None:
        """Omitting a required field (id) raises pydantic.ValidationError."""
        with pytest.raises(ValidationError):
            Patch(
                workspace_slug="ws1",
                branch_name="feat/x",
                position=1,
                status="active",
                added_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
            )

    def test_patch_missing_all_required_raises_validation_error(self) -> None:
        """Constructing Patch with no arguments raises pydantic.ValidationError."""
        with pytest.raises(ValidationError):
            Patch()


# ---------------------------------------------------------------------------
# TS-01-34: RebuildJob model validates required fields (id, status, created_at)
#           and defaults all other fields to None
# ---------------------------------------------------------------------------


class TestRebuildJobModel:
    """TS-01-34 -- RebuildJob model validates required fields (id, status,
    created_at) and defaults optional fields (strategy, error, patch_results,
    integration_head_sha, previous_integration_head_sha, completed_at) to None.

    Requirements: 01-REQ-5.4, 01-REQ-5.E4
    """

    def test_rebuild_job_required_fields_parse(self) -> None:
        """RebuildJob accepts the three required fields and returns a valid instance."""
        job = RebuildJob(
            id="job-1",
            status="pending",
            created_at="2026-01-01T00:00:00Z",
        )
        assert job.id == "job-1"
        assert job.status == "pending"
        assert job.created_at == "2026-01-01T00:00:00Z"

    def test_rebuild_job_optional_fields_default_to_none(self) -> None:
        """All optional fields on RebuildJob default to None when not provided."""
        job = RebuildJob(
            id="job-1",
            status="pending",
            created_at="2026-01-01T00:00:00Z",
        )
        assert job.strategy is None
        assert job.error is None
        assert job.patch_results is None
        assert job.integration_head_sha is None
        assert job.previous_integration_head_sha is None
        assert job.completed_at is None

    def test_rebuild_job_optional_fields_accept_values(self) -> None:
        """Optional fields accept explicit values."""
        patch_result_data = {
            "patch_id": "p1",
            "branch_name": "feat/x",
            "position": 1,
            "status": "applied",
        }
        job = RebuildJob(
            id="job-1",
            status="completed",
            created_at="2026-01-01T00:00:00Z",
            strategy="merge",
            error="some error",
            patch_results=[patch_result_data],
            integration_head_sha="def456",
            previous_integration_head_sha="abc123",
            completed_at="2026-01-01T01:00:00Z",
        )
        assert job.strategy == "merge"
        assert job.error == "some error"
        assert job.patch_results is not None
        assert len(job.patch_results) == 1
        assert job.integration_head_sha == "def456"
        assert job.previous_integration_head_sha == "abc123"
        assert job.completed_at == "2026-01-01T01:00:00Z"

    def test_rebuild_job_missing_required_field_raises_validation_error(self) -> None:
        """Omitting a required field (id) raises pydantic.ValidationError."""
        with pytest.raises(ValidationError):
            RebuildJob(
                status="pending",
                created_at="2026-01-01T00:00:00Z",
            )

    def test_rebuild_job_missing_all_required_raises_validation_error(self) -> None:
        """Constructing RebuildJob with no arguments raises pydantic.ValidationError."""
        with pytest.raises(ValidationError):
            RebuildJob()

    def test_rebuild_job_non_string_id_raises_validation_error(self) -> None:
        """Non-string id (e.g. integer) raises pydantic.ValidationError (01-REQ-5.E4)."""
        with pytest.raises(ValidationError):
            RebuildJob(
                id=12345,
                status="pending",
                created_at="2026-01-01T00:00:00Z",
            )


# ---------------------------------------------------------------------------
# Model tests for PatchResult, SyncResult, RerereEntry
# ---------------------------------------------------------------------------


class TestPatchResultModel:
    """PatchResult model -- field parsing and extra='ignore' behavior.

    Requirements: 01-REQ-5.1
    """

    def test_patch_result_fields_parse(self) -> None:
        """PatchResult accepts its declared fields and returns a valid instance."""
        pr = PatchResult(
            patch_id="p1",
            branch_name="feat/x",
            position=1,
            status="applied",
        )
        assert pr.patch_id == "p1"
        assert pr.status == "applied"

    def test_patch_result_extra_field_discarded(self) -> None:
        """PatchResult silently discards unknown fields."""
        pr = PatchResult(
            patch_id="p1",
            branch_name="feat/x",
            position=1,
            status="applied",
            new_field="surprise",
        )
        assert not hasattr(pr, "new_field")


class TestSyncResultModel:
    """SyncResult model -- field parsing and extra='ignore' behavior.

    Requirements: 01-REQ-5.1
    """

    def test_sync_result_fields_parse(self) -> None:
        """SyncResult accepts its declared fields and returns a valid instance."""
        sr = SyncResult(
            patches_merged=["feat/already-merged"],
            rebuild_triggered=True,
            rebuild_job_id="job-1",
            force_push_detected=False,
        )
        assert sr.rebuild_triggered is True
        assert sr.rebuild_job_id == "job-1"

    def test_sync_result_extra_field_discarded(self) -> None:
        """SyncResult silently discards unknown fields."""
        sr = SyncResult(
            patches_merged=[],
            rebuild_triggered=False,
            force_push_detected=False,
            unexpected_field="gone",
        )
        assert not hasattr(sr, "unexpected_field")


class TestRerereEntryModel:
    """RerereEntry model -- field parsing and extra='ignore' behavior.

    Requirements: 01-REQ-5.1
    """

    def test_rerere_entry_fields_parse(self) -> None:
        """RerereEntry accepts its declared fields and returns a valid instance."""
        entry = RerereEntry(
            path="src/foo.py",
            recorded_at="2026-01-01T00:00:00Z",
        )
        assert entry.path == "src/foo.py"
        assert entry.recorded_at == "2026-01-01T00:00:00Z"

    def test_rerere_entry_extra_field_discarded(self) -> None:
        """RerereEntry silently discards unknown fields."""
        entry = RerereEntry(
            path="src/foo.py",
            recorded_at="2026-01-01T00:00:00Z",
            future_field="discarded",
        )
        assert not hasattr(entry, "future_field")


# ---------------------------------------------------------------------------
# PatchStatusDashboard with nested PatchDetail and PatchSummary
# ---------------------------------------------------------------------------


class TestPatchStatusDashboardModel:
    """PatchStatusDashboard model with nested PatchDetail and PatchSummary.

    Requirements: 01-REQ-5.1, 01-REQ-5.E3
    """

    def test_dashboard_with_nested_patch_detail(self) -> None:
        """PatchStatusDashboard accepts patches as list of PatchDetail dicts."""
        dashboard = PatchStatusDashboard(
            patches=[
                {
                    "id": "p1",
                    "branch_name": "feat/x",
                    "position": 1,
                    "status": "active",
                    "last_rebuild_result": "applied",
                    "conflict_files": None,
                    "description": "my patch",
                }
            ],
            summary={
                "total_patches": 1,
                "active": 1,
                "merged_upstream": 0,
                "conflict": 0,
                "disabled": 0,
                "total_rerere_resolutions": 0,
            },
        )
        assert len(dashboard.patches) == 1
        assert isinstance(dashboard.patches[0], PatchDetail)
        assert dashboard.patches[0].id == "p1"

    def test_dashboard_summary_is_patch_summary(self) -> None:
        """PatchStatusDashboard.summary is a PatchSummary instance."""
        dashboard = PatchStatusDashboard(
            patches=[],
            summary={
                "total_patches": 0,
                "active": 0,
                "merged_upstream": 0,
                "conflict": 0,
                "disabled": 0,
            },
        )
        assert isinstance(dashboard.summary, PatchSummary)


class TestPatchSummaryModel:
    """PatchSummary model -- total_rerere_resolutions defaults to 0.

    Requirements: 01-REQ-5.E3
    """

    def test_total_rerere_resolutions_defaults_to_zero(self) -> None:
        """PatchSummary defaults total_rerere_resolutions to 0 when omitted (01-REQ-5.E3)."""
        summary = PatchSummary(
            total_patches=5,
            active=3,
            merged_upstream=1,
            conflict=1,
            disabled=0,
        )
        assert summary.total_rerere_resolutions == 0

    def test_total_rerere_resolutions_accepts_value(self) -> None:
        """PatchSummary accepts an explicit total_rerere_resolutions value."""
        summary = PatchSummary(
            total_patches=5,
            active=3,
            merged_upstream=1,
            conflict=1,
            disabled=0,
            total_rerere_resolutions=7,
        )
        assert summary.total_rerere_resolutions == 7


class TestPatchDetailModel:
    """PatchDetail model -- per-patch detail within a PatchStatusDashboard.

    Requirements: 01-REQ-5.1
    """

    def test_patch_detail_required_fields_parse(self) -> None:
        """PatchDetail accepts required fields and returns a valid instance."""
        pd = PatchDetail(
            id="p1",
            branch_name="feat/x",
            position=1,
            status="active",
            last_rebuild_result="applied",
        )
        assert pd.id == "p1"
        assert pd.branch_name == "feat/x"
        assert pd.last_rebuild_result == "applied"

    def test_patch_detail_optional_description_defaults_to_none(self) -> None:
        """PatchDetail.description defaults to None when not provided."""
        pd = PatchDetail(
            id="p1",
            branch_name="feat/x",
            position=1,
            status="active",
            last_rebuild_result=None,
        )
        assert pd.description is None

    def test_patch_detail_conflict_files_defaults_to_none(self) -> None:
        """PatchDetail.conflict_files defaults to None when not provided."""
        pd = PatchDetail(
            id="p1",
            branch_name="feat/x",
            position=1,
            status="active",
            last_rebuild_result=None,
        )
        assert pd.conflict_files is None

    def test_patch_detail_extra_field_discarded(self) -> None:
        """PatchDetail silently discards unknown fields."""
        pd = PatchDetail(
            id="p1",
            branch_name="feat/x",
            position=1,
            status="active",
            last_rebuild_result=None,
            new_api_field="ignored",
        )
        assert not hasattr(pd, "new_api_field")


# ---------------------------------------------------------------------------
# RebuildPreview with nested RebuildPreviewPatchResult
# ---------------------------------------------------------------------------


class TestRebuildPreviewModel:
    """RebuildPreview model with nested RebuildPreviewPatchResult.

    Requirements: 01-REQ-5.1
    """

    def test_rebuild_preview_with_patch_results(self) -> None:
        """RebuildPreview accepts patch_results list."""
        preview = RebuildPreview(
            patch_results=[
                {
                    "patch_id": "p1",
                    "branch_name": "feat/x",
                    "position": 1,
                    "status": "would_succeed",
                },
                {
                    "patch_id": "p2",
                    "branch_name": "feat/y",
                    "position": 2,
                    "status": "would_conflict",
                    "conflict_files": ["src/bar.py"],
                },
            ],
        )
        assert len(preview.patch_results) == 2
        assert isinstance(preview.patch_results[0], RebuildPreviewPatchResult)
        assert preview.patch_results[0].status == "would_succeed"
        assert preview.patch_results[1].status == "would_conflict"

    def test_rebuild_preview_empty_patch_results(self) -> None:
        """RebuildPreview accepts an empty patch_results list."""
        preview = RebuildPreview(patch_results=[])
        assert preview.patch_results == []

    def test_rebuild_preview_extra_field_discarded(self) -> None:
        """RebuildPreview silently discards unknown fields."""
        preview = RebuildPreview(
            patch_results=[],
            new_field="gone",
        )
        assert not hasattr(preview, "new_field")


class TestRebuildPreviewPatchResultModel:
    """RebuildPreviewPatchResult model -- per-patch preview result.

    Requirements: 01-REQ-5.1
    """

    def test_rebuild_preview_patch_result_fields_parse(self) -> None:
        """RebuildPreviewPatchResult accepts its declared fields."""
        r = RebuildPreviewPatchResult(
            patch_id="p1",
            branch_name="feat/x",
            position=1,
            status="would_succeed",
        )
        assert r.patch_id == "p1"
        assert r.status == "would_succeed"

    def test_rebuild_preview_patch_result_optional_fields(self) -> None:
        """RebuildPreviewPatchResult optional fields default appropriately."""
        r = RebuildPreviewPatchResult(
            patch_id="p1",
            branch_name="feat/x",
            position=1,
            status="would_succeed",
        )
        assert r.tree_sha is None
        assert r.conflict_files is None


# ---------------------------------------------------------------------------
# TS-01-35: All model classes are importable from the afhub top-level namespace
# ---------------------------------------------------------------------------


class TestTopLevelExports:
    """TS-01-35 -- All model classes are importable directly from the afhub
    top-level namespace.

    Requirements: 01-REQ-5.5
    """

    def test_all_models_importable_from_afhub(self) -> None:
        """All listed model classes can be imported from afhub without ImportError."""
        from afhub import (
            Patch,
            PatchDetail,
            PatchResult,
            PatchStatusDashboard,
            PatchSummary,
            RebuildJob,
            RebuildPreview,
            RebuildPreviewPatchResult,
            RerereEntry,
            SyncResult,
            Workspace,
        )

        assert all(
            cls is not None
            for cls in [
                Workspace,
                Patch,
                RebuildJob,
                PatchResult,
                SyncResult,
                RerereEntry,
                PatchStatusDashboard,
                PatchDetail,
                PatchSummary,
                RebuildPreview,
                RebuildPreviewPatchResult,
            ]
        )

    def test_top_level_workspace_is_models_workspace(self) -> None:
        """afhub.Workspace is the same class as afhub.models.Workspace."""
        from afhub import Workspace as TopLevelWorkspace
        from afhub.models import Workspace as ModelsWorkspace

        assert TopLevelWorkspace is ModelsWorkspace

    def test_top_level_rebuild_job_is_models_rebuild_job(self) -> None:
        """afhub.RebuildJob is the same class as afhub.models.RebuildJob."""
        from afhub import RebuildJob as TopLevelRebuildJob
        from afhub.models import RebuildJob as ModelsRebuildJob

        assert TopLevelRebuildJob is ModelsRebuildJob


# ---------------------------------------------------------------------------
# TS-01-36: afhub.models does not define a Variable model
# ---------------------------------------------------------------------------


class TestNoVariableModel:
    """TS-01-36 -- afhub.models does not define a Variable model; importing
    afhub.models does not expose a Variable class.

    Requirements: 01-REQ-5.6
    """

    def test_no_variable_attr_on_models_module(self) -> None:
        """afhub.models has no Variable attribute."""
        import afhub.models as m

        assert not hasattr(m, "Variable")

    def test_import_variable_from_models_raises_import_error(self) -> None:
        """Attempting to import Variable from afhub.models raises ImportError."""
        with pytest.raises(ImportError):
            from afhub.models import Variable  # noqa: F401
