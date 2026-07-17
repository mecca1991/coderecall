# CodeRecall

CodeRecall is a local CLI for developers who want to check their understanding of a code change before they share it.

It analyzes the current Git branch, asks a few targeted questions about the changed code, and writes a local report that helps the developer prepare for review.

## Status

CodeRecall is in early project setup. The runnable CLI implementation is not yet available in this repository.

This README should stay focused on installation, setup, usage, and contribution details for developers working with the public project.

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

There is no package installation step yet because the implementation has not been added.

When the CLI lands, this section should include:

- Required language/runtime version.
- Dependency installation command.
- Local test command.
- Local lint/typecheck command.
- How to run the CLI from source.

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
