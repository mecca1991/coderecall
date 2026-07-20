"""Install CodeRecall's opt-in advisory pre-push hook."""

from __future__ import annotations

from pathlib import Path

import typer

from coderecall.cli.error_rendering import exit_with_error
from coderecall.core.errors import CodeRecallError
from coderecall.git import GitAdapter
from coderecall.hooks import (
    HookInstallationStatus,
    HookInstaller,
    build_pre_push_hook,
)


def install_hook_command(
    base: str | None = typer.Option(
        None,
        "--base",
        help="Validate and store an explicit review base; omit for runtime selection.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=("Replace a changed CodeRecall-managed hook. Unmanaged hooks are always preserved."),
    ),
) -> None:
    """Install an advisory pre-push hook; bypass it with git push --no-verify."""

    git = GitAdapter(Path.cwd().resolve())
    try:
        repository = git.detect_repository()
        selected_base = git.select_base_branch(repository, base) if base is not None else None
        hook_path = git.resolve_pre_push_hook_path(repository)
    except CodeRecallError as error:
        exit_with_error(error)

    typer.echo(f'Hook path: "{hook_path}"')
    if selected_base is None:
        typer.echo("Review base: automatic (runtime configuration, then main/master inference)")
    else:
        typer.echo(f"Review base: {selected_base} (explicit)")
    typer.echo("Advisory: declining, no terminal, or review failure continues the push.")
    typer.echo("Bypass: git push --no-verify")

    try:
        result = HookInstaller().install(
            hook_path,
            build_pre_push_hook(selected_base),
            force=force,
        )
    except CodeRecallError as error:
        exit_with_error(error)

    if result.status is HookInstallationStatus.INSTALLED:
        typer.echo(f'Installed CodeRecall pre-push hook: "{result.path}"')
    elif result.status is HookInstallationStatus.ALREADY_CURRENT:
        typer.echo(f'CodeRecall pre-push hook is already current: "{result.path}"')
    else:
        typer.echo(f'Updated CodeRecall pre-push hook: "{result.path}"')
