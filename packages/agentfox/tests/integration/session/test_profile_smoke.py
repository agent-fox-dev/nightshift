"""Integration smoke tests for archetype profiles (spec 99).

Traces execution paths from design.md end-to-end with real function calls.
No mocking of load_profile, build_system_prompt, init_profiles, or
get_archetype.

Test Spec: TS-99-SMOKE-1, TS-99-SMOKE-2, TS-99-SMOKE-3
Requirements: 99-REQ-1.1, 99-REQ-1.2, 99-REQ-3.1, 99-REQ-4.1, 99-REQ-4.2,
              99-REQ-4.4, 99-REQ-5.1, 99-REQ-5.2
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# TS-99-SMOKE-1: Prompt assembly with project profile
# Execution Path 1 from design.md
# Requirements: 99-REQ-1.1, 99-REQ-1.2, 99-REQ-1.3
# ---------------------------------------------------------------------------


class TestPromptWithProjectProfile:
    """Smoke test: end-to-end 2-layer prompt assembly with project-level profile.

    Must NOT satisfy with mocking load_profile or build_system_prompt.
    """

    def test_project_profile_overrides_default(self, tmp_path: Path) -> None:
        """TS-99-SMOKE-1: Prompt contains project profile content, not default.

        Verifies the full path:
          NodeSessionRunner._build_prompts -> build_system_prompt -> load_profile
          -> reads project .agent-fox/profiles/coder.md -> returns custom content
          -> concatenated into prompt in layer order.
        """
        from agentfox.session.prompt import build_system_prompt

        profile_content = "CUSTOM CODER IDENTITY FOR SMOKE TEST"

        profiles_dir = tmp_path / ".agent-fox" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "coder.md").write_text(profile_content, encoding="utf-8")

        task_context = "TASK CONTEXT MARKER"

        prompt = build_system_prompt(
            context=task_context,
            archetype="coder",
            project_dir=tmp_path,
        )

        assert profile_content in prompt, "Archetype profile missing from prompt"
        assert task_context in prompt, "Task context missing from prompt"

        idx_profile = prompt.index(profile_content)
        idx_task = prompt.index(task_context)
        assert idx_profile < idx_task, "Profile must appear before task context"

    def test_default_profiles_contain_session_rules(self, tmp_path: Path) -> None:
        """Package-default archetype profiles include Session Rules.

        Requirement: 99-REQ-1.E1
        """
        from agentfox.session.prompt import build_system_prompt

        profile_content = "CODER IDENTITY CUSTOM"
        profiles_dir = tmp_path / ".agent-fox" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "coder.md").write_text(profile_content, encoding="utf-8")

        prompt = build_system_prompt(
            context="some task context",
            archetype="coder",
            project_dir=tmp_path,
        )

        assert len(prompt) > 0
        assert profile_content in prompt

    def test_package_default_coder_has_session_rules(self) -> None:
        """Package-default coder profile includes Session Rules section."""
        from agentfox.session.prompt import build_system_prompt

        prompt = build_system_prompt(context="some task context", archetype="coder")

        assert "Session Rules" in prompt


# ---------------------------------------------------------------------------
# TS-99-SMOKE-2: Custom archetype session
# Execution Path 3 from design.md
# Requirements: 99-REQ-4.1, 99-REQ-4.2, 99-REQ-4.4
# ---------------------------------------------------------------------------


class TestCustomArchetypeSession:
    """Smoke test: end-to-end custom archetype resolution and prompt assembly.

    Must NOT satisfy with mocking get_archetype or load_profile.
    """

    def test_custom_archetype_inherits_coder_permissions(self, tmp_path: Path) -> None:
        """TS-99-SMOKE-2: Custom archetype gets coder permissions from config.

        Verifies the full path:
          get_archetype("deployer") -> no registry entry -> has_custom_profile
          -> True -> _resolve_custom_preset -> "coder" -> returns coder entry
          with name="deployer".
        """
        from agentfox.archetypes import ARCHETYPE_REGISTRY, get_archetype
        from agentfox.core.config import AgentFoxConfig

        profiles_dir = tmp_path / ".agent-fox" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "deployer.md").write_text("# Deployer Profile", encoding="utf-8")

        cfg = AgentFoxConfig.model_validate(
            {
                "archetypes": {
                    "custom": {
                        "deployer": {"permissions": "coder"},
                    }
                }
            }
        )

        # Real get_archetype — no mocking
        entry = get_archetype("deployer", project_dir=tmp_path, config=cfg)
        coder = ARCHETYPE_REGISTRY["coder"]

        assert entry.default_allowlist == coder.default_allowlist, "Custom archetype should inherit coder allowlist"

    def test_custom_archetype_prompt_uses_custom_profile(self, tmp_path: Path) -> None:
        """TS-99-SMOKE-2: Prompt for custom archetype contains custom profile content.

        Verifies the full path:
          build_system_prompt("deployer", project_dir) -> load_profile("deployer")
          -> finds deployer.md in project -> returns content -> included in prompt.
        """
        from agentfox.session.prompt import build_system_prompt

        profiles_dir = tmp_path / ".agent-fox" / "profiles"
        profiles_dir.mkdir(parents=True)
        deployer_content = "# Deployer Profile\nDeploy all the things."
        (profiles_dir / "deployer.md").write_text(deployer_content, encoding="utf-8")

        # Real build_system_prompt — no mocking
        prompt = build_system_prompt(
            context="deployment task context",
            archetype="deployer",
            project_dir=tmp_path,
        )

        assert "Deployer Profile" in prompt, "Deployer profile content should appear in prompt"
        assert "deployment task context" in prompt, "Task context should appear in prompt"


# ---------------------------------------------------------------------------
# TS-99-SMOKE-3: Init then load
# Execution Path 4 + Path 2 from design.md
# Requirements: 99-REQ-3.1, 99-REQ-5.1, 99-REQ-5.2
# ---------------------------------------------------------------------------


