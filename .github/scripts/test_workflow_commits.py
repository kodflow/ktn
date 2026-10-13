#!/usr/bin/env python3
"""Every commit in the licence workflow tolerates having nothing to commit.

A step that runs `git commit` under `set -euo pipefail` FAILS when the index is
clean, and in license-roster.yml a failed step is not cosmetic: the `sign` job
re-signs the roster only when one of the jobs it needs SUCCEEDED. The public
`Commit` step of `publish` was the one commit without the guard every other
step has, so re-applying `license:approved` to an issue whose key is already
published — the documented way to record a new label decision, such as a
`ciOwnerAlso:` grant — wrote the private state, then died on "nothing to
commit", and the roster carrying that decision was never signed by that run.

Read as text rather than as YAML: the suite is stdlib-only by design, and the
property is about the shell lines themselves.
"""
import pathlib
import re
import unittest

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / "workflows" / "license-roster.yml"
GUARD = "git diff --cached --quiet || git commit"


class WorkflowCommitGuardTest(unittest.TestCase):
    """A clean index must be a no-op, never a failed step."""

    def commit_lines(self) -> list:
        """Every non-comment line of the workflow that runs `git commit`."""
        lines = WORKFLOW.read_text().splitlines()
        return [
            (number, line.strip())
            for number, line in enumerate(lines, start=1)
            if re.search(r"\bgit commit\b", line) and not line.strip().startswith("#")
        ]

    def test_the_workflow_commits_at_all(self):
        """The control: a reader that found nothing would pass every row below."""
        self.assertGreater(len(self.commit_lines()), 0)

    def test_every_commit_is_guarded_against_a_clean_index(self):
        """THE ROW. An identical re-approval must not fail the publish job."""
        unguarded = [f"line {n}: {text}" for n, text in self.commit_lines() if GUARD not in text]

        self.assertEqual(
            unguarded, [],
            "a `git commit` with nothing staged fails the step under set -e, and a failed "
            "publish job skips the roster signature",
        )


if __name__ == "__main__":
    unittest.main()
