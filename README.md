# CodeRecall

CodeRecall is a local CLI for developers who want to check their understanding of a code change before they share it.

It analyzes the current Git branch, asks a few targeted questions about the changed code, and writes a local report that helps the developer prepare for review.

## Status

CodeRecall is in early project setup. The Python CLI scaffold is available, but the full review workflow is still under active development.

## Intended Usage

Once implemented, the main workflow will be:

```bash
coderecall review --base main
```

The command will:

1. Compare the current branch against a base branch.
2. Summarize the meaningful code changes.
3. Ask open-ended questions about behavior, failure modes, and evidence.
4. Evaluate answers against repository evidence.
5. Write a local Markdown report.

Optional pre-push hook support is planned:

```bash
coderecall install-hook
```

The hook is intended to be opt-in and bypassable.

## Development Setup

Clone the repository:

```bash
git clone https://github.com/mecca1991/coderecall.git
cd coderecall
```

CodeRecall requires Python 3.11 or newer.

Recommended setup with `uv`:

```bash
uv sync
uv run coderecall --help
```

Standard `pip` setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
coderecall --help
```

Run tests:

```bash
uv run pytest
```

Run linting:

```bash
uv run ruff check .
```

Run type checking:

```bash
uv run mypy
```

## Local Files

CodeRecall reports are local developer artifacts and should not be committed:

```text
coderecall-report.md
.coderecall/
```

Internal planning and brainstorming documents should live under `docs/` and are ignored by default.

## Project Direction

CodeRecall is designed as a developer learning tool, not a surveillance or productivity scoring tool.

The product should remain:

- Local-first.
- Developer-owned.
- Evidence-grounded.
- Short by default.
- Respectful in feedback.

## License

CodeRecall is released under the [MIT License](./LICENSE). It may be used for personal, academic, or commercial projects.
