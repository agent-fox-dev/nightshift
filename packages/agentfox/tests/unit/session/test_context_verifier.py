"""Tests for verification checklist injection into verifier context.

Verifies that assemble_context() includes the verification checklist
section when archetype is 'verifier' and omits it for other archetypes.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
from agentfox.knowledge.migrations import run_migrations


def _make_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    run_migrations(conn)
    return conn


def _setup_spec(tmp_path: Path) -> Path:
    spec_dir = tmp_path / "10_my_spec"
    spec_dir.mkdir()
    (spec_dir / "prd.md").write_text(
        '---\nspec_id: "10"\nspec_name: "my_spec"\ntitle: "My Spec"\nstatus: "draft"\n'
        'created_at: "2024-01-01T00:00:00Z"\nupdated_at: "2024-01-01T00:00:00Z"\n'
        'owner: "t"\nsource: "t"\nschema_version: 1\n---\n# My Spec\n',
        encoding="utf-8",
    )
    (spec_dir / "requirements.json").write_text(
        json.dumps(
            {
                "spec_id": "10",
                "spec_name": "my_spec",
                "schema_version": 1,
                "introduction": "REQ",
                "glossary": {},
                "requirements": [
                    {
                        "id": "REQ-1",
                        "title": "First requirement",
                        "user_story": {"role": "user", "action": "do X", "benefit": "value"},
                        "acceptance_criteria": [
                            {
                                "id": "10-REQ-1.1",
                                "ears_pattern": "ubiquitous",
                                "system": "the system",
                                "action": "SHALL do X",
                            }
                        ],
                        "edge_cases": [],
                    }
                ],
                "correctness_properties": [],
                "execution_paths": [],
                "error_handling": [],
            }
        ),
        encoding="utf-8",
    )
    (spec_dir / "test_spec.json").write_text(
        json.dumps(
            {
                "spec_id": "10",
                "spec_name": "my_spec",
                "schema_version": 1,
                "test_cases": [],
                "property_tests": [],
                "edge_case_tests": [],
                "smoke_tests": [],
                "coverage": {"requirements_covered": [], "properties_covered": [], "paths_covered": [], "gaps": []},
            }
        ),
        encoding="utf-8",
    )
    (spec_dir / "tasks.json").write_text(
        json.dumps(
            {
                "spec_id": "10",
                "spec_name": "my_spec",
                "schema_version": 1,
                "test_commands": {"spec_tests": "", "all_tests": "", "linter": ""},
                "dependencies": [],
                "task_groups": [
                    {
                        "id": 1,
                        "kind": "tests",
                        "title": "Write tests",
                        "subtasks": [
                            {"id": "1.1", "title": "Unit tests", "state": "done"},
                            {"id": "1.2", "title": "Integration tests", "state": "pending"},
                        ],
                    }
                ],
                "traceability": [],
            }
        ),
        encoding="utf-8",
    )
    return spec_dir
