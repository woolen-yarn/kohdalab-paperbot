"""Release publishing guards, with gh/git replaced by a fake (nothing is published)."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_distribution as p

SHA = "a" * 40
ENV = {
    "GITHUB_REPOSITORY": "owner/iv",
    "GITHUB_SHA": SHA,
    "GITHUB_RUN_ID": "9",
    "GITHUB_RUN_NUMBER": "12",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REF_NAME": "main",
}


class FakeGitHub:
    """Records commands and mimics gh/git for one fresh, non-existent tag."""

    def __init__(self, tamper=False, existing=False):
        self.calls, self.assets = [], {}
        self.tamper, self.existing, self.published = tamper, existing, False

    def __call__(self, command, check=True):
        self.calls.append(command)
        out, code, err = "", 0, ""
        if command[:2] == ["git", "rev-parse"]:
            out = SHA
        elif command[:2] == ["git", "archive"]:
            Path(command[command.index("-o") + 1]).write_bytes(b"source")
        elif command[:2] == ["gh", "api"] and "/commits/" in command[2]:
            out = json.dumps({"sha": SHA})
        elif command[:2] == ["gh", "api"]:
            code, err = (0, "") if self.existing else (1, "gh: Not Found (HTTP 404)")
        elif command[:3] == ["gh", "release", "create"]:
            for name in command[command.index("--notes-file") + 2 :]:
                path = Path(name)
                digest = "bad" if self.tamper else p.sha256(path)
                self.assets[path.name] = {
                    "name": path.name, "size": path.stat().st_size, "digest": f"sha256:{digest}"
                }
        elif command[:3] == ["gh", "release", "edit"]:
            self.published = True
        elif command[:3] == ["gh", "release", "view"]:
            out = json.dumps({
                "assets": list(self.assets.values()), "isDraft": not self.published,
                "tagName": "build-12-2", "targetCommitish": SHA, "url": "https://example/r",
            })
        return subprocess.CompletedProcess(command, code, out, err)

    def ran(self, *prefix):
        return any(call[: len(prefix)] == list(prefix) for call in self.calls)


class PublishDistributionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        (self.root / "Cargo.toml").write_text('[workspace.package]\nversion = "1.0.2"\n')
        for name in ("iv-1.0.2-windows-x64.zip", "check.json", "iv-cli.exe", "notes.txt"):
            (self.dist / name).write_text(name)
        self.args = argparse.Namespace(
            directory=self.dist, portable_glob="iv-*-windows-x64.zip", alias="iv-windows-x64.zip",
            source_alias=None,
        )

    def publish(self, fake, environ=ENV):
        with patch.dict(p.os.environ, environ, clear=True), patch.object(p, "run", fake), \
                patch.object(p, "ROOT", self.root), \
                patch.object(p, "STAGE", self.root / "build" / "distribution-stage"):
            p.publish(self.args)

    def test_publishes_only_after_inventory_and_digests_match(self):
        fake = FakeGitHub()
        self.publish(fake)
        self.assertEqual(sorted(fake.assets), [
            "BUILD-INFO.json", "SHA256SUMS.txt", "check.json", "iv-1.0.2-windows-x64.zip",
            "iv-build-source.zip", "iv-windows-x64.zip",
        ])
        self.assertIn(["gh", "release", "edit", "build-12-2", "--repo", "owner/iv",
                       "--draft=false", "--latest"], fake.calls)
        info = json.loads((self.root / "build/distribution-stage/BUILD-INFO.json").read_text())
        self.assertEqual(
            (info["version"], info["signing"], info["hardware"], info["qualification"]),
            ("1.0.2", "unsigned", "not tested", "software-only"),
        )

    def test_existing_fixed_archive_name_is_valid(self):
        portable = self.dist / "iv-source.zip"
        portable.write_bytes(b"tracked-source")
        self.args.portable_glob = portable.name
        self.args.alias = portable.name
        self.publish(FakeGitHub())
        self.assertEqual(
            (self.root / "build/distribution-stage/iv-source.zip").read_bytes(),
            b"tracked-source",
        )

    def test_digest_mismatch_leaves_draft_unpublished(self):
        fake = FakeGitHub(tamper=True)
        with self.assertRaisesRegex(p.PublishError, "digest mismatch"):
            self.publish(fake)
        self.assertTrue(fake.ran("gh", "release", "create"))
        self.assertFalse(fake.ran("gh", "release", "edit"))

    def test_existing_release_is_never_replaced(self):
        fake = FakeGitHub(existing=True)
        with self.assertRaisesRegex(p.PublishError, "already exists"):
            self.publish(fake)
        self.assertFalse(fake.ran("gh", "release", "create"))

    def test_other_branches_are_refused(self):
        fake = FakeGitHub()
        with self.assertRaisesRegex(p.PublishError, "only 'main'"):
            self.publish(fake, {**ENV, "GITHUB_REF_NAME": "feature"})
        self.assertEqual(fake.calls, [])

    def test_portable_must_match_exactly_once(self):
        (self.dist / "iv-1.0.1-windows-x64.zip").write_text("old")
        with self.assertRaisesRegex(p.PublishError, "exactly one portable"):
            self.publish(FakeGitHub())

    def test_inventory_reports_missing_unexpected_and_undigested_assets(self):
        expected = {"a.zip": (1, "11"), "b.json": (2, "22")}
        assets = [{"name": "a.zip", "size": 1, "digest": None},
                  {"name": "c.zip", "size": 1, "digest": "sha256:33"}]
        self.assertEqual(p.inventory_problems(expected, assets), [
            "missing asset b.json", "unexpected asset c.zip",
            "digest mismatch a.zip: None != sha256:11",
        ])


if __name__ == "__main__":
    unittest.main()
