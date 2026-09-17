#!/usr/bin/env python3
"""Tests for record_version.py — the floor that gets signed.

Whatever lands in required-version.txt is carried by the signed roster as the
minimum version allowed to run, so it reaches every client authenticated. Two
ways that goes wrong: a value the client cannot parse (which fails open there,
silently), and a value that goes DOWN (which un-mandates an update that was
already mandatory, equally silently).
"""
import importlib.util
import os
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("record_version.py")


def load_script():
    """Import record_version.py by path; it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("record_version", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordVersionTest(unittest.TestCase):
    """What may be written, and in which direction."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.licenses = pathlib.Path(self.tmp.name) / "licenses"
        self.licenses.mkdir()
        os.environ["LICENSES_DIR"] = str(self.licenses)
        for key in ("LICENSES_DIR", "VERSION"):
            self.addCleanup(os.environ.pop, key, None)
        self.module = load_script()
        self.floor = self.licenses / self.module.FLOOR

    def dispatch(self, version):
        """Deliver one release-published event."""
        os.environ["VERSION"] = version
        self.module.main()

    def test_the_first_release_sets_the_floor(self):
        self.dispatch("v1.8.13")

        self.assertEqual(self.floor.read_text().strip(), "v1.8.13")

    def test_a_newer_release_raises_it(self):
        self.dispatch("v1.8.13")

        self.dispatch("v1.9.0")

        self.assertEqual(self.floor.read_text().strip(), "v1.9.0")

    def test_an_older_release_arriving_late_does_not_lower_it(self):
        """THE defect: repository_dispatch is fire-and-forget.

        A retried delivery, a replayed payload or two releases in quick
        succession arrive in an order that is not the order they were released
        in. The last writer used to win, which un-mandates an update nobody
        gets told about.
        """
        self.dispatch("v1.9.0")

        self.dispatch("v1.8.14")

        self.assertEqual(self.floor.read_text().strip(), "v1.9.0")

    def test_ten_is_not_below_nine(self):
        """A string comparison would put v1.10.0 under v1.9.0 and lower the floor."""
        self.dispatch("v1.9.0")

        self.dispatch("v1.10.0")

        self.assertEqual(self.floor.read_text().strip(), "v1.10.0")

    def test_a_prerelease_does_not_displace_its_release(self):
        """v1.10.0-rc.1 is BELOW v1.10.0, and would otherwise un-mandate it."""
        self.dispatch("v1.10.0")

        self.dispatch("v1.10.0-rc.1")

        self.assertEqual(self.floor.read_text().strip(), "v1.10.0")

    def test_a_release_supersedes_its_own_prerelease(self):
        self.dispatch("v2.0.0-rc.2")

        self.dispatch("v2.0.0")

        self.assertEqual(self.floor.read_text().strip(), "v2.0.0")

    def test_prereleases_are_ordered_the_way_semver_says(self):
        """Numeric identifiers rank below alphanumeric ones, and rc.2 beats rc.1."""
        self.assertLess(
            self.module.precedence("v1.0.0-1"), self.module.precedence("v1.0.0-alpha")
        )
        self.assertLess(
            self.module.precedence("v1.0.0-rc.1"), self.module.precedence("v1.0.0-rc.2")
        )
        self.assertLess(
            self.module.precedence("v1.0.0-rc"), self.module.precedence("v1.0.0-rc.1")
        )

    def test_an_empty_dispatch_is_refused(self):
        """Blanking the floor disables mandatory updates with no message."""
        with self.assertRaises(SystemExit) as raised:
            self.dispatch("")

        self.assertNotEqual(raised.exception.code, 0)
        self.assertFalse(self.floor.exists())

    def test_the_shapes_that_a_client_would_misread_are_refused(self):
        """Each of these passed the old shell regex.

        A floor is compared with SemVer on the client, where a malformed value
        fails open — so the grammar is checked here, where it is one run.
        """
        for version in ("v01.2.3", "v1.2.3-", "v1.2.3+build", "1.2.3", "v1.2", "vX.Y.Z"):
            with self.subTest(version=version):
                self.setUp()
                with self.assertRaises(SystemExit):
                    self.dispatch(version)
                self.assertFalse(self.floor.exists())

    def test_a_corrupt_recorded_floor_is_replaced(self):
        """Every client is already reading something it cannot parse.

        Preserving it would be preserving the outage; the valid value wins and
        the run says it did.
        """
        self.floor.write_text("latest\n")

        self.dispatch("v1.9.0")

        self.assertEqual(self.floor.read_text().strip(), "v1.9.0")


if __name__ == "__main__":
    unittest.main()
