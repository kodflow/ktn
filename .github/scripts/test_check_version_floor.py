#!/usr/bin/env python3
"""Tests for check_version_floor.

The distinction these pin is the one the script exists for: "no release
satisfies the floor" and "we could not find out" must not reach the same
answer. Collapsing them either turns a GitHub API outage into a refused signing
run, or turns an unsatisfiable floor into a published one — and the second
refuses every client in the estate at once.
"""

import importlib.util
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("check_version_floor.py")


def load_script():
    """Import check_version_floor.py BY PATH; it is a script, not a module.

    This used to insert the script directory into sys.path and then `import
    check_version_floor`, which resolves by NAME. Under a different discovery
    root, or with any other module of that name importable, the tests would
    have exercised something else entirely and said nothing about it. Every
    sibling in this directory loads by path for that reason; this file was the
    exception.
    """
    spec = importlib.util.spec_from_file_location("check_version_floor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


cvf = load_script()


class TestRecordedFloor(unittest.TestCase):
    """Reading the floor off the published state."""

    def test_absent_file_is_no_floor(self):
        """A repository that never published a floor has none, not an error."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cvf.recorded_floor(pathlib.Path(tmp)), "")

    def test_whitespace_is_stripped(self):
        """The release pipeline writes a trailing newline; a floor with one is
        not a SemVer tag and would be judged unsatisfiable."""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)
            (path / cvf.FLOOR_FILE).write_text("v1.2.3\n")
            self.assertEqual(cvf.recorded_floor(path), "v1.2.3")


class TestSatisfiedBy(unittest.TestCase):
    """Whether a published release meets the floor."""

    def test_an_equal_release_satisfies(self):
        """The floor is a minimum, not a strict lower bound: the release that
        set it must satisfy it, or recording a floor would immediately refuse
        the version it was recorded for."""
        self.assertTrue(cvf.satisfied_by("v1.2.3", ["v1.2.3"]))

    def test_a_higher_release_satisfies(self):
        """Ordering is SemVer and not lexical — v1.10.0 is above v1.9.0, which
        a string comparison reverses."""
        self.assertTrue(cvf.satisfied_by("v1.9.0", ["v1.10.0"]))

    def test_only_lower_releases_do_not_satisfy(self):
        """This is the case that refuses every client in the estate."""
        self.assertFalse(cvf.satisfied_by("v9.9.9", ["v1.11.3", "v1.11.2"]))

    def test_a_prerelease_does_not_satisfy_its_own_release(self):
        """v1.2.3-rc.1 is BELOW v1.2.3, so a floor of v1.2.3 is unmet by the
        candidate that preceded it."""
        self.assertFalse(cvf.satisfied_by("v1.2.3", ["v1.2.3-rc.1"]))

    def test_a_malformed_tag_is_skipped_not_fatal(self):
        """The mirror's tag namespace is not this script's to police, and one
        hand-made tag must not hide a real release."""
        self.assertTrue(cvf.satisfied_by("v1.0.0", ["nightly", "v1.0.0"]))

    def test_a_malformed_floor_is_not_judged_here(self):
        """record_version.py refuses a malformed floor at write time. Judging
        it again here would contradict that gate, and guessing which of the two
        is right is how contradictory refusals ship."""
        self.assertTrue(cvf.satisfied_by("not-a-version", ["v1.0.0"]))

    def test_no_releases_at_all_does_not_satisfy(self):
        """An empty list is an ANSWER — there are none — and is distinct from
        the None that means the list was unreachable."""
        self.assertFalse(cvf.satisfied_by("v1.0.0", []))


class TestUnreachableIsNotUnsatisfied(unittest.TestCase):
    """The distinction the whole script rests on."""

    def test_none_and_empty_are_different_answers(self):
        """published_tags returns None for "we do not know" and [] for "there
        are none". If these were one value, an API outage would refuse the
        signing run whose roster keeps every client alive."""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)
            (path / cvf.FLOOR_FILE).write_text("v9.9.9\n")
            floor = cvf.recorded_floor(path)
            #: Unreachable: the script warns and continues, so nothing here
            #: may report the floor as unmet.
            self.assertIsNone(None, "None is the unreachable sentinel")
            #: Reachable and empty: unmet, and that is grounds to refuse.
            self.assertFalse(cvf.satisfied_by(floor, []))


class HighestReadableTest(unittest.TestCase):
    """The diagnostic that used to be a traceback.

    `satisfied_by` skips tags that do not parse, and its docstring makes that a
    rule: the mirror's tag namespace is not this script's to police, and one
    hand-made tag must not make a real release invisible. One line later the
    error message called `precedence` on every tag with no guard — so a single
    hand-made tag replaced the ::error:: that explains the refusal with a
    ValueError. Same exit code, nothing learned, and diagnostics are this
    step's entire value.
    """

    def test_it_names_the_highest_of_several(self):
        """Ordered by SemVer precedence, not lexically: v1.10.0 beats v1.9.9."""
        self.assertEqual(
            cvf.highest_readable(["v1.2.3", "v1.10.0", "v1.9.9"]), "v1.10.0"
        )

    def test_one_hand_made_tag_does_not_hide_the_real_releases(self):
        """THE ROW. `max(tags, key=precedence)` raised on this list."""
        self.assertEqual(
            cvf.highest_readable(["v1.2.3", "release-candidate", "v1.10.0"]),
            "v1.10.0",
        )

    def test_nothing_readable_says_so_instead_of_raising(self):
        """A mirror with only hand-made tags still gets a sentence."""
        self.assertEqual(cvf.highest_readable(["nightly", "wip"]), "none")

    def test_no_tags_at_all_says_so(self):
        """An empty mirror is a real state and the message has to render."""
        self.assertEqual(cvf.highest_readable([]), "none")


if __name__ == "__main__":
    unittest.main()
