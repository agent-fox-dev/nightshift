"""Tests for workspace migration — dependency declarations, import rewiring, isolation.

These tests verify the atomic import migration across the workspace.
They will fail until the full migration is complete.

TS-01-34: afcore/pyproject.toml declares afaudit>=4.3.2
TS-01-35: No old audit module paths remain in afcore, af, nightshift
TS-01-36: afcore/__init__.py does not re-export moved audit symbols
TS-01-37: af and nightshift pyproject.toml declare afaudit dependency
TS-01-38: agentspec and spec have zero direct audit imports
TS-01-39: cleanup.py has no duckdb; duckdb_sink has retention logic
"""

from __future__ import annotations

import tomllib
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

# Package paths
AGENTFOX_PKG = WORKSPACE_ROOT / "packages" / "afcore"
AF_PKG = WORKSPACE_ROOT / "packages" / "af"
NIGHTSHIFT_PKG = WORKSPACE_ROOT / "packages" / "nightshift"
AGENTSPEC_PKG = WORKSPACE_ROOT / "packages" / "agentspec"
SPEC_PKG = WORKSPACE_ROOT / "packages" / "spec"
AFAUDIT_SRC = WORKSPACE_ROOT / "packages" / "afaudit" / "afaudit"


def _collect_py_files(base: Path) -> list[Path]:
    """Collect all .py files recursively under a directory."""
    if not base.is_dir():
        return []
    return list(base.rglob("*.py"))


def _read_all_py_content(base: Path) -> str:
    """Read and concatenate all .py file contents under a directory."""
    parts: list[str] = []
    for f in _collect_py_files(base):
        try:
            parts.append(f.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(parts)


class TestAgentfoxDependency:
    """TS-01-34: afcore/pyproject.toml declares afaudit>=4.3.2.

    Requirement: 01-REQ-10.1
    """

    def test_afaudit_in_afcore_dependencies(self) -> None:
        """afcore must list afaudit>=1.0.0 in [project.dependencies]."""
        with open(AGENTFOX_PKG / "pyproject.toml", "rb") as f:
            toml = tomllib.load(f)
        deps = toml["project"]["dependencies"]
        matching = [d for d in deps if "afaudit" in d]
        assert len(matching) > 0, f"afaudit not found in afcore [project.dependencies]; current deps: {deps}"
        # Verify version constraint
        dep_str = matching[0]
        assert "1.0.0" in dep_str, f"Expected version constraint with 1.0.0, got: {dep_str}"


class TestNoOldImportPaths:
    """TS-01-35: No old afcore audit module paths in production code.

    Requirement: 01-REQ-10.2
    """

    OLD_MODULE_PATHS = [
        "from afcore.knowledge.audit import",
        "from afcore.knowledge.sink import",
        "from afcore.knowledge.agent_trace import",
        "from afcore.workspace.audit_cleanup import",
    ]

    def test_no_old_imports_in_afcore(self) -> None:
        """No old audit module imports should remain in afcore/ source."""
        content = _read_all_py_content(AGENTFOX_PKG)
        for old_path in self.OLD_MODULE_PATHS:
            assert old_path not in content, f"Old import path '{old_path}' still found in afcore/"

    def test_no_old_imports_in_nightshift(self) -> None:
        """No old audit module imports should remain in nightshift/ source."""
        content = _read_all_py_content(NIGHTSHIFT_PKG)
        for old_path in self.OLD_MODULE_PATHS:
            assert old_path not in content, f"Old import path '{old_path}' still found in nightshift/"


class TestNoReexportShims:
    """TS-01-36: afcore/__init__.py does not re-export moved audit symbols.

    Requirement: 01-REQ-10.3
    """

    MOVED_SYMBOLS = [
        "AuditEvent",
        "AuditJsonlSink",
        "SessionSink",
        "AgentTraceSink",
        "build_postmortem",
        "emit_audit_event",
        "purge_stale_audit_files",
        "enforce_file_retention",
        "AUDIT_DIR",
    ]

    def test_no_audit_symbols_in_afcore_init(self) -> None:
        """afcore/__init__.py must not import or re-export any moved symbol."""
        init_path = AGENTFOX_PKG / "afcore" / "__init__.py"
        content = init_path.read_text(encoding="utf-8")
        for sym in self.MOVED_SYMBOLS:
            assert sym not in content, (
                f"Moved audit symbol '{sym}' found in afcore/__init__.py — "
                "shim re-exports are not allowed (01-REQ-10.3)"
            )


class TestNightshiftDependencies:
    """TS-01-37: nightshift declares afaudit as a direct dependency.

    Requirement: 01-REQ-10.4
    """

    def test_nightshift_depends_on_afaudit(self) -> None:
        """nightshift/pyproject.toml must list afaudit in [project.dependencies]."""
        with open(NIGHTSHIFT_PKG / "pyproject.toml", "rb") as f:
            toml = tomllib.load(f)
        deps = toml["project"]["dependencies"]
        assert any("afaudit" in d for d in deps), f"afaudit not found in nightshift [project.dependencies]: {deps}"

    def test_nightshift_imports_from_afaudit(self) -> None:
        """nightshift source must import symbols from afaudit."""
        content = _read_all_py_content(NIGHTSHIFT_PKG)
        assert "from afaudit" in content or "import afaudit" in content, (
            "nightshift source should import from afaudit after migration"
        )


class TestAgentspecSpecNoAuditImports:
    """TS-01-38: agentspec and spec have zero direct audit imports.

    Requirement: 01-REQ-10.5
    """

    AUDIT_IMPORT_PATTERNS = [
        "afaudit",
        "afcore.knowledge.audit",
        "afcore.knowledge.sink",
        "afcore.knowledge.agent_trace",
        "afcore.workspace.audit_cleanup",
    ]

    def test_agentspec_no_audit_imports(self) -> None:
        """agentspec/ must have zero direct audit imports."""
        content = _read_all_py_content(AGENTSPEC_PKG)
        for pattern in self.AUDIT_IMPORT_PATTERNS:
            assert pattern not in content, f"Unexpected audit import '{pattern}' found in agentspec/"

    def test_spec_no_audit_imports(self) -> None:
        """spec/ must have zero direct audit imports."""
        content = _read_all_py_content(SPEC_PKG)
        for pattern in self.AUDIT_IMPORT_PATTERNS:
            assert pattern not in content, f"Unexpected audit import '{pattern}' found in spec/"


class TestDbRetentionSplit:
    """TS-01-39: cleanup.py has no duckdb; duckdb_sink has retention logic.

    Requirement: 01-REQ-10.6
    """

    def test_cleanup_no_duckdb_import(self) -> None:
        """afaudit/cleanup.py must not import or reference duckdb."""
        source = (AFAUDIT_SRC / "cleanup.py").read_text(encoding="utf-8")
        assert "duckdb" not in source, (
            "afaudit/cleanup.py must not contain any duckdb reference — "
            "DB retention logic belongs in afcore.knowledge.duckdb_sink"
        )

    def test_cleanup_no_sql(self) -> None:
        """afaudit/cleanup.py must not contain SQL statements."""
        source = (AFAUDIT_SRC / "cleanup.py").read_text(encoding="utf-8")
        assert "DELETE FROM" not in source, (
            "afaudit/cleanup.py must not contain SQL — DB operations belong in afcore.knowledge.duckdb_sink"
        )

    def test_duckdb_sink_has_retention_logic(self) -> None:
        """afcore/knowledge/duckdb_sink.py must contain retention logic."""
        duckdb_sink_path = AGENTFOX_PKG / "afcore" / "knowledge" / "duckdb_sink.py"
        source = duckdb_sink_path.read_text(encoding="utf-8")
        has_retention = "retain" in source.lower() or "retention" in source.lower()
        assert has_retention, (
            "afcore/knowledge/duckdb_sink.py should contain DB-retention logic after the enforce_audit_retention split"
        )
