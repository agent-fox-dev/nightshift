"""nightshift --init command: create config and provision required platform labels."""

from __future__ import annotations

import click


def handle_init(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    """Eager callback for --init: write config and provision required platform labels."""
    if not value or ctx.resilient_parsing:
        return

    import asyncio
    from pathlib import Path

    from afcore.core.config import load_config
    from afcore.core.config_gen import generate_local_config_template
    from afissues.labels import REQUIRED_LABELS

    config_path = Path.cwd() / ".nightshift" / "config.toml"

    if config_path.exists():
        click.echo(f"Config already exists at {config_path} — skipping.")
    else:
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(generate_local_config_template(), encoding="utf-8")
            click.echo(f"Created config: {config_path}")
        except OSError as exc:
            click.echo(f"Warning: could not write config: {exc}", err=True)

    from afcore.nightshift.platform_factory import create_platform_safe

    config = load_config()
    platform = create_platform_safe(config, Path.cwd())

    if platform is None:
        click.echo(
            "Warning: platform not configured — skipping label provisioning.\n"
            "  Set [platform] type and the appropriate token env variable\n"
            "  (GITHUB_PAT / GITLAB_TOKEN / GITEA_TOKEN) to create required labels.",
            err=True,
        )
    else:

        async def _provision() -> None:
            for spec in REQUIRED_LABELS:
                await platform.create_label(spec.name, spec.color, spec.description)
                click.echo(f"  label: {spec.name}")
            if hasattr(platform, "close"):
                await platform.close()

        asyncio.run(_provision())
        click.echo("Required labels provisioned.")

    ctx.exit(0)
