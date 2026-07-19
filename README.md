# CodeRecall

CodeRecall is a local CLI for developers who want to check their understanding of a code
change before they share it.

It analyzes the current Git branch, asks targeted questions about the changed code, and captures
the developer's answers locally to help them prepare for review.

## Status

CodeRecall is in early development. The Python CLI can inspect a branch, render a local,
evidence-based diff summary, generate branch-specific questions, capture terminal answers,
evaluate them against repository evidence, ask one targeted follow-up when useful, and write a
local Markdown report with practical review talking points.

## Intended Usage

The main workflow is:

```bash
coderecall review --base main
```

Git repository detection, base selection, change collection, low-signal file filtering, lightweight
change modeling, likely side-effect detection, concise diff summaries, question generation, and
terminal answer capture, grounded evaluation, adaptive follow-up, and local reporting are
available.

By default, `review` compares commits on the current branch with their merge base on the selected branch. To also include staged and unstaged changes to tracked files, run:

```bash
coderecall review --base main --include-uncommitted
```

Untracked files are excluded until they are added to Git. The command summarizes the meaningful
files, changed symbols, related tests, likely side effects, and analysis uncertainty. It then asks
three questions by default, in behavior, failure, and evidence order. Choose one to three questions
with:

```bash
coderecall review --base main --questions 2
```

Answers may span multiple lines. A blank line submits the current answer, while pressing Enter
immediately records an explicit skip. End-of-file safely submits any partial answer and skips the
remaining questions.

In an interactive terminal, CodeRecall uses restrained styling for headings, question categories,
warnings, and answer status. Every label and message remains present without color, and redirected
output automatically falls back to unstyled text. To explicitly disable ANSI styling and terminal
detection, use:

```bash
coderecall review --base main --plain
```

Styled and plain sessions use the same wording and section order, so color never carries meaning.

### Local Report

Every completed review session overwrites `coderecall-report.md` in the directory where the
command was invoked. The report contains the change summary, questions, answers, assessments,
repository citations, any follow-up response, and a review-talking-points section. Sessions that
stop before questions are available do not write a report.

Completed reports contain one to three deterministic preparation notes: a change explanation,
the most important repository-grounded gap (or a detected side-effect risk when no gap exists),
and the strongest evidence the developer demonstrably referenced. Follow-up responses remain
separate until follow-up assessment is supported. Talking points are developer-owned preparation,
not grades, scores, or manager-facing evaluation.

Choose a different local path with `--report`; missing parent directories are created:

```bash
coderecall review --base main --report .coderecall/reports/latest.md
```

Reports are written as UTF-8 Markdown and remain on the local filesystem. CodeRecall does not
upload or share them.

### Assessment Labels

Answer evaluation uses a stable, non-numeric assessment vocabulary:

- `Strong`: The answer matches repository evidence and covers the important reasoning.
- `Partial`: The answer is directionally correct but misses a relevant detail.
- `Gap found`: The answer conflicts with repository evidence or misses a critical failure mode.
- `Uncertain`: CodeRecall cannot confidently evaluate the answer from available evidence.

These labels describe evidence-supported understanding rather than scoring the developer. An
`Uncertain` assessment can explain the missing or insufficient evidence in uncertainty notes.

The review context excludes generated output, vendored dependencies, lockfiles, and minified assets from analysis by default. Filtered paths remain visible in the command output with the reason they were excluded.

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
