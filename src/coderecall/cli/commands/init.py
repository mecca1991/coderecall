"""Create repository-local starter configuration."""

from __future__ import annotations

from pathlib import Path

import typer

from coderecall.cli.error_rendering import exit_with_error
from coderecall.config import CONFIG_FILENAME, STARTER_CONFIG, anchor_path
from coderecall.core.errors import CodeRecallError, ConfigInitializationFailed
from coderecall.git import GitAdapter


def init_command(
    path: Path | None = typer.Option(
        None,
        "--path",
        help="Explicit path where the starter config will be written.",
    ),
) -> None:
    """Create a starter CodeRecall config file."""

    invocation_directory = Path.cwd().resolve()
    try:
        repository = GitAdapter(invocation_directory).detect_repository()
        target = (
            repository.root / CONFIG_FILENAME
            if path is None
            else anchor_path(path, invocation_directory)
        )
        _write_starter_config(target)
    except CodeRecallError as error:
        exit_with_error(error)

    typer.echo(f'Created CodeRecall configuration: "{target}"')


def _write_starter_config(target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ConfigInitializationFailed(
            f'Could not prepare the starter config path "{target}": {error}',
            recovery="Choose a writable `--path` and run `coderecall init` again.",
        ) from error

    try:
        with target.open("x", encoding="utf-8", newline="\n") as config_file:
            config_file.write(STARTER_CONFIG)
    except FileExistsError as error:
        raise ConfigInitializationFailed(
            f'CodeRecall will not overwrite existing path "{target}".',
            recovery="Choose a different `--path` or remove the existing path first.",
        ) from error
    except OSError as error:
        raise ConfigInitializationFailed(
            f'Could not write the starter config to "{target}": {error}',
            recovery="Choose a writable `--path` and run `coderecall init` again.",
        ) from error
