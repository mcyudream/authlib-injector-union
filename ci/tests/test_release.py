import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("release", Path(__file__).resolve().parents[1] / "release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="union-release-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.root_patch = patch.object(release, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.git_patch = patch.object(release, "git", side_effect=self.fake_git)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        self.env_patch = patch.dict(os.environ, {"GITHUB_REF": "refs/tags/v1.4.0", "AI_VERSION_NUMBER": "1.4.0"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.status = ""
        self.tags = "v1.4.0"
        (self.root / "gradle.properties").write_text("version=1.4.0\n")
        (self.root / "build/libs").mkdir(parents=True)
        (self.root / "build/publications/agent").mkdir(parents=True)
        self.make_inputs()

    def fake_git(self, *args):
        return {("status", "--porcelain"): self.status, ("tag", "--points-at", "HEAD"): self.tags,
                ("rev-parse", "HEAD"): "a" * 40}[args]

    def make_inputs(self, version="1.4.0", major=52, extra=None, manifest=None):
        attrs = {"Manifest-Version": "1.0", "Implementation-Title": release.ARTIFACT,
                 "Implementation-Version": version, "Premain-Class": "moe.yushi.authlibinjector.Premain",
                 "Git-Commit": "a" * 40, "Git-IsClean": "false" if version.endswith(".dirty") else "true"}
        attrs.update(manifest or {})
        names = release.filenames(version)
        with zipfile.ZipFile(self.root / "build/libs" / names[0], "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", "\r\n".join(key + ": " + value for key, value in attrs.items()) + "\r\n")
            jar.writestr("moe/yushi/authlibinjector/internal/org/objectweb/asm/ClassReader.class",
                         struct.pack(">IHH", 0xCAFEBABE, 0, major))
            if extra:
                jar.writestr(extra, struct.pack(">IHH", 0xCAFEBABE, 0, 52))
        with zipfile.ZipFile(self.root / "build/libs" / names[1], "w") as jar:
            jar.writestr("moe/yushi/authlibinjector/yggdrasil/PublicKeyManager.java", "// fixture")
        self.pom = self.root / "build/publications/agent/pom-default.xml"
        self.pom.write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
                            '<groupId>online.yudream</groupId><artifactId>authlib-injector-union</artifactId>'
                            '<version>' + version + '</version></project>')

    def test_matching_tag(self):
        self.assertEqual(release.check_tag(), "1.4.0")

    def test_wrong_ref(self):
        for ref in ("refs/heads/develop", "refs/tags/v1.4.1", "refs/tags/v1.4.0-rc1"):
            with self.subTest(ref=ref), patch.dict(os.environ, {"GITHUB_REF": ref}), self.assertRaises(ValueError):
                release.check_tag()

    def test_missing_tag(self):
        self.tags = "v1.3.0"
        with self.assertRaises(ValueError):
            release.check_tag()

    def test_dirty_checkout(self):
        for status in (" M build.gradle", "?? untracked-secret"):
            self.status = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                release.check_tag()

    def test_version_override(self):
        with patch.dict(os.environ, {"AI_VERSION_NUMBER": "1.4.1"}), self.assertRaises(ValueError):
            release.check_tag()

    def test_version_syntax(self):
        for value in ("01.4.0", "1.4.0-SNAPSHOT", "../evil", "1.4.0\nversion=1.4.1"):
            (self.root / "gradle.properties").write_text("version=" + value + "\n")
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.configured_version()

    def test_bundle_and_verify(self):
        version, directory, names = release.bundle(True)
        self.assertEqual(version, "1.4.0")
        self.assertEqual(len(names), 3)
        self.assertEqual(release.verify_bundle(True)[0], version)
        self.assertTrue((directory / "SHA256SUMS").is_file())

    def test_dirty_bundle_allowed_only_for_ci(self):
        self.make_inputs(version="1.4.0.dirty")
        self.assertEqual(release.bundle(False)[0], "1.4.0.dirty")
        with self.assertRaises(ValueError):
            release.verify_bundle(True)

    def test_wrong_pom_coordinate(self):
        self.pom.write_text(self.pom.read_text().replace("online.yudream", "other.group"))
        with self.assertRaises(ValueError):
            release.bundle(True)

    def test_no_runtime_dependencies(self):
        self.pom.write_text(self.pom.read_text().replace("</project>", "<dependencies><dependency /></dependencies></project>"))
        with self.assertRaises(ValueError):
            release.bundle(True)

    def test_manifest_version(self):
        self.make_inputs(manifest={"Implementation-Version": "1.4.0.dirty"})
        with self.assertRaises(ValueError):
            release.bundle(True)

    def test_manifest_commit(self):
        self.make_inputs(manifest={"Git-Commit": "b" * 40})
        with self.assertRaises(ValueError):
            release.bundle(True)

    def test_manifest_clean_flag(self):
        self.make_inputs(manifest={"Git-IsClean": "false"})
        with self.assertRaises(ValueError):
            release.bundle(True)

    def test_class_target(self):
        self.make_inputs(major=61)
        with self.assertRaises(ValueError):
            release.bundle(True)

    def test_fixture_and_unshaded_classes(self):
        for name in ("com/mojang/authlib/Fake.class", "org/objectweb/asm/ClassReader.class",
                     "moe/yushi/authlibinjector/yggdrasil/PublicKeyManagerTest$1.class"):
            self.make_inputs(extra=name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                release.bundle(True)

    def test_changed_bytes(self):
        _, directory, names = release.bundle(True)
        with (directory / names[0]).open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaises(ValueError):
            release.verify_bundle(True)

    def test_unexpected_file(self):
        _, directory, _ = release.bundle(True)
        (directory / "unexpected.txt").write_text("unexpected")
        with self.assertRaises(ValueError):
            release.verify_bundle(True)

    def test_missing_file(self):
        _, directory, names = release.bundle(True)
        (directory / names[1]).unlink()
        with self.assertRaises(ValueError):
            release.verify_bundle(True)

    def test_checksum_path_and_duplicates(self):
        _, directory, names = release.bundle(True)
        original = (directory / "SHA256SUMS").read_text()
        for changed in (original.replace(names[0], "../escape"), original + original.splitlines()[0] + "\n"):
            (directory / "SHA256SUMS").write_text(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                release.verify_bundle(True)

    def test_preflight_requires_all_files_absent(self):
        release.bundle(True)
        with patch.object(release, "remote_file", return_value=(404, b"")) as remote:
            self.assertEqual(release.preflight(), "1.4.0")
            self.assertEqual(remote.call_count, 3)

    def test_preflight_fails_closed(self):
        release.bundle(True)
        for status in (200, 301, 401, 403, 429, 500, 503):
            with self.subTest(status=status), patch.object(release, "remote_file", return_value=(status, b"")) as remote:
                with self.assertRaises(ValueError):
                    release.preflight()
                self.assertEqual(remote.call_count, 1)

    def test_published_download_verification(self):
        _, directory, _ = release.bundle(True)
        with patch.object(release, "remote_file", side_effect=lambda version, name, method, limit: (200, (directory / name).read_bytes())), \
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            self.assertEqual(release.verify_published(), "1.4.0")

    def test_published_mismatch(self):
        release.bundle(True)
        with patch.object(release, "remote_file", return_value=(200, b"changed")), self.assertRaises(ValueError):
            release.verify_published()

    def test_missing_credentials_never_requests(self):
        with patch.dict(os.environ, {"NEXUS_USERNAME": "", "NEXUS_PASSWORD": ""}), \
                patch.object(release.urllib.request, "build_opener") as opener, self.assertRaises(ValueError):
            release.remote_file("1.4.0", release.filenames("1.4.0")[0], "HEAD")
        opener.assert_not_called()

    def test_redirects_never_forward_credentials(self):
        self.assertIsNone(release.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))

    def test_manifest_line_folding(self):
        self.assertEqual(release.manifest_attributes(b"Name: abc\r\n def\r\n"), {"Name": "abcdef"})

    def test_wrapper_has_pinned_distribution_checksum(self):
        wrapper = Path(__file__).resolve().parents[2] / "gradle/wrapper/gradle-wrapper.properties"
        properties = dict(line.split("=", 1) for line in wrapper.read_text().splitlines() if "=" in line and not line.startswith("#"))
        self.assertRegex(properties["distributionSha256Sum"], r"^[0-9a-f]{64}$")
        self.assertEqual(properties["distributionUrl"], r"https\://downloads.gradle.org/distributions/gradle-8.0.2-bin.zip")


if __name__ == "__main__":
    unittest.main()
