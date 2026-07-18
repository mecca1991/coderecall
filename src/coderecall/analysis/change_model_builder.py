"""Build a bounded model of the meaningful branch changes."""

from __future__ import annotations

from coderecall.core.types import ChangeContext, DiffCollection, RepositoryContext


class ChangeModelBuilder:
    """Transform collected Git evidence into a change context."""

    def build(
        self,
        repository: RepositoryContext,
        base_branch: str,
        diff: DiffCollection,
    ) -> ChangeContext:
        """Preserve collected evidence in an immutable analysis context."""

        return ChangeContext(
            repo_root=repository.root,
            current_branch=repository.current_branch,
            base_branch=base_branch,
            merge_base=diff.merge_base,
            changed_files=diff.changed_files,
            filtered_files=diff.filtered_files,
            diff_hunks=diff.diff_hunks,
            uncertainty_notes=diff.uncertainty_notes,
        )
