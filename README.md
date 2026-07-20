# CodeRecall

Check that you understand a code change before you share it.

## What It Does

CodeRecall compares the current Git branch with a base branch, summarizes the meaningful changes,
and asks up to three questions about behavior, failure modes, and evidence. It evaluates answers
against local repository evidence and may ask one targeted follow-up. A completed session writes a
local Markdown report containing the summary, answers, assessments, citations, any follow-up, and
practical review talking points.

## Install

CodeRecall requires Python 3.11 or newer. It is not published on PyPI; install it directly from
GitHub with either `uv` or `pipx`:

```bash
uv tool install git+https://github.com/mecca1991/coderecall.git
```

```bash
pipx install git+https://github.com/mecca1991/coderecall.git
```

## Quick Start

Run a review from anywhere inside your Git working tree:

```bash
coderecall review --base main
```

An abridged session looks like this:

```text
Privacy
Model mode: Local heuristic (no remote model)
Repository content, answers, and reports stay on this machine.
CodeRecall sends no telemetry and makes no network requests.

CodeRecall review
Branch: feature/payment-retry -> main
Changes: 3 total, 3 analyzed, 0 filtered

Change summary
Purpose: Likely updates the payment retry flow across 3 meaningful files.
Likely side effects:
  - network call: The changed code likely makes a network or external service call.

Questions
Question 1/3 — Behavior
What behavior does the changed payment service introduce, and how does it affect the surrounding flow?
Answer:

...

Session complete
Answers: 3 answered, 0 skipped
Report written: "/path/to/repository/coderecall-report.md"
```

## Usage

`coderecall review` supports:

| Option | Behavior |
| --- | --- |
| `--base <branch>` | Compare against an explicit base; otherwise infer `main` or `master`. |
| `--report <path>` | Write the local Markdown report to a custom path (default: `coderecall-report.md`). |
| `--questions <1-3>` | Choose the number of questions; the default is three. |
| `--no-follow-up` | Disable the adaptive follow-up question. |
| `--include-uncommitted` | Include staged and unstaged changes to tracked files. |
| `--no-include-uncommitted` | Review committed changes only. |
| `--plain` | Disable styled terminal output. |

Untracked files are excluded until they are added to Git. Generated directories, vendored
dependencies, lockfiles, and minified assets are filtered from analysis and reported with their
filter reason.

### Project Configuration

Run `coderecall init` to create `.coderecall.yml` at the repository root:

```yaml
base: main
report_path: coderecall-report.md
questions: 3
include_uncommitted: false
exclude:
  - node_modules/**
  - dist/**
  - build/**
  - vendor/**
```

Command-line options override configuration values. `exclude` accepts positive
Git-ignore-style patterns and adds them to CodeRecall's built-in filtering.

### Assessment Labels

| Label | Meaning |
| --- | --- |
| `Strong` | Matches repository evidence and covers the important reasoning. |
| `Partial` | Directionally correct but misses a relevant detail. |
| `Gap found` | Conflicts with evidence or misses a critical failure mode. |
| `Uncertain` | Available evidence cannot support a confident evaluation. |

Assessments are evidence-grounded preparation feedback, not numeric scores.

### Optional Pre-Push Hook

Install the advisory hook with `coderecall install-hook`. Use `--base <branch>` to store an
explicit base and `--force` to replace changed CodeRecall-managed hook content. Existing unmanaged
hooks and symbolic links are preserved. Declining the prompt, lacking a terminal, or encountering
a review error always allows the push to continue; `git push --no-verify` bypasses the hook
entirely.

## Privacy

**CodeRecall reviews are local-only.** The implemented model mode is
`Local heuristic (no remote model)`: repository content, answers, assessments, and reports remain
on the machine running CodeRecall. The CLI sends no telemetry, makes no network requests, and does
not upload or share reports.

## Development

```bash
git clone https://github.com/mecca1991/coderecall.git
cd coderecall
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## License

CodeRecall is available under the [MIT License](./LICENSE).
