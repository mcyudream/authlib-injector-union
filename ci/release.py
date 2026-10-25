#!/usr/bin/env python3
"""Validate and promote the tested agent. Credentials never enter files or output."""
import argparse
import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
GROUP = "online.yudream"
ARTIFACT = "authlib-injector-union"
REPOSITORY = "https://nexus.yudream.online/repository/maven-releases"
SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
NS = {"m": "http://maven.apache.org/POM/4.0.0"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def configured_version():
    versions = re.findall(r"^version\s*=\s*(\S+)\s*$", (ROOT / "gradle.properties").read_text(), re.M)
    require(len(versions) == 1 and re.fullmatch(SEMVER, versions[0]), "Expected one X.Y.Z version in gradle.properties")
    return versions[0]


def check_tag():
    version = configured_version()
    tag = "v" + version
    require(os.environ.get("GITHUB_REF", "refs/tags/" + tag) == "refs/tags/" + tag,
            "Release ref must be refs/tags/vX.Y.Z matching gradle.properties")
    require(os.environ.get("AI_VERSION_NUMBER", version) == version, "Version overrides must match gradle.properties")
    require(tag in git("tag", "--points-at", "HEAD").splitlines(), "Release tag must point at the checked-out commit")
    require(not git("status", "--porcelain"), "Release checkout must be clean, including untracked files")
    return version


def pom_version(path):
    pom = ET.parse(path).getroot()
    require(pom.findtext("m:groupId", namespaces=NS) == GROUP, "Incorrect Maven group")
    require(pom.findtext("m:artifactId", namespaces=NS) == ARTIFACT, "Incorrect Maven artifact")
    version = pom.findtext("m:version", namespaces=NS) or ""
    require(re.fullmatch(SEMVER + r"(?:\.dirty)?", version), "Invalid publication version")
    require(version in (configured_version(), configured_version() + ".dirty"), "POM version differs from project version")
    require(pom.findtext("m:packaging", default="jar", namespaces=NS) == "jar", "Publication must be a JAR")
    require(not pom.findall("m:dependencies/m:dependency", NS), "Shaded agent must not publish runtime dependencies")
    return version


def filenames(version):
    stem = ARTIFACT + "-" + version
    return [stem + ".jar", stem + "-sources.jar", stem + ".pom"]


def manifest_attributes(data):
    lines = []
    for line in data.decode("utf-8").replace("\r\n", "\n").splitlines():
        if line.startswith(" ") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return dict(line.split(": ", 1) for line in lines if ": " in line)


def verify_jar(path, version, release):
    with zipfile.ZipFile(path) as jar:
        attrs = manifest_attributes(jar.read("META-INF/MANIFEST.MF"))
        require(attrs.get("Implementation-Title") == ARTIFACT, "Unexpected artifact title")
        require(attrs.get("Implementation-Version") == version, "JAR/POM version mismatch")
        require(attrs.get("Premain-Class") == "moe.yushi.authlibinjector.Premain", "Missing agent entry point")
        require(attrs.get("Git-Commit") == git("rev-parse", "HEAD"), "JAR was built from a different commit")
        if release:
            require(attrs.get("Git-IsClean") == "true" and ".dirty" not in version, "Dirty JAR cannot be released")
        entries = jar.namelist()
        require("moe/yushi/authlibinjector/internal/org/objectweb/asm/ClassReader.class" in entries, "Missing shaded ASM")
        for name in entries:
            require(not name.startswith(("com/mojang/", "com/velocitypowered/", "net/md_5/", "union/smoke/",
                                         "moe/yushi/authlibinjector/test/", "org/objectweb/asm/")),
                    "Unshaded dependency or test fixture in agent")
            if name.endswith(".class"):
                require(not re.search(r"Test(?:\$[^/]*)?\.class$", name), "Test class in agent")
                with jar.open(name) as stream:
                    magic, _, major = struct.unpack(">IHH", stream.read(8))
                require(magic == 0xCAFEBABE and major <= 52, "Agent contains non-Java-8 bytecode")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_bundle(release=False):
    directory = ROOT / "build/release"
    poms = list(directory.glob("*.pom"))
    require(len(poms) == 1, "Bundle must contain exactly one POM")
    version = pom_version(poms[0])
    if release:
        require(version == check_tag(), "Bundle must match release tag")
    names = filenames(version)
    require({p.name for p in directory.iterdir()} == set(names + ["SHA256SUMS"]), "Unexpected or missing bundle files")
    expected = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9.-]+)", line)
        require(match is not None, "Invalid checksum entry")
        value, name = match.groups()
        require(name in names and name not in expected, "Unexpected or duplicate checksum entry")
        expected[name] = value
    require(set(expected) == set(names), "Incomplete checksums")
    for name in names:
        require(digest(directory / name) == expected[name], "Bundle checksum mismatch: " + name)
    verify_jar(directory / names[0], version, release)
    with zipfile.ZipFile(directory / names[1]) as sources:
        require("moe/yushi/authlibinjector/yggdrasil/PublicKeyManager.java" in sources.namelist(), "Missing agent sources")
    return version, directory, names


def bundle(release):
    pom = ROOT / "build/publications/agent/pom-default.xml"
    version = pom_version(pom)
    if release:
        require(version == check_tag(), "Publication version must match tag")
    names = filenames(version)
    directory = ROOT / "build/release"
    directory.mkdir(parents=True, exist_ok=True)
    require({p.name for p in directory.iterdir()} <= set(names + ["SHA256SUMS"]), "Remove stale build/release output before bundling another version")
    for name in names[:2]:
        shutil.copyfile(ROOT / "build/libs" / name, directory / name)
    shutil.copyfile(pom, directory / names[2])
    (directory / "SHA256SUMS").write_text("".join(digest(directory / name) + "  " + name + "\n" for name in names))
    return verify_bundle(release)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward deployment credentials to a redirect destination.
        return None


def remote_file(version, name, method, limit=0):
    require(re.fullmatch(SEMVER, version) and name in filenames(version), "Invalid release path")
    username, password = os.environ.get("NEXUS_USERNAME"), os.environ.get("NEXUS_PASSWORD")
    require(username and password, "Set NEXUS_USERNAME and NEXUS_PASSWORD as Actions secrets")
    url = REPOSITORY + "/" + GROUP.replace(".", "/") + "/" + ARTIFACT + "/" + version + "/" + name
    auth = base64.b64encode((username + ":" + password).encode()).decode()
    request = urllib.request.Request(url, method=method, headers={"Authorization": "Basic " + auth})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            return response.status, response.read(limit + 1) if method == "GET" else b""
    except urllib.error.HTTPError as error:
        return error.code, b""
    except urllib.error.URLError as error:
        raise ValueError("Nexus transport request failed; no credentials were logged") from None


def preflight():
    version, _, names = verify_bundle(True)
    for name in names:
        status, _ = remote_file(version, name, "HEAD")
        require(status == 404, "Refusing publish: " + name + " returned HTTP " + str(status) + " (only 404 permits a new version)")
    return version


def verify_published():
    version, directory, names = verify_bundle(True)
    for name in names:
        local = (directory / name).read_bytes()
        status, body = remote_file(version, name, "GET", len(local))
        require(status == 200 and hashlib.sha256(body).digest() == hashlib.sha256(local).digest(),
                "Published artifact verification failed: " + name + " (HTTP " + str(status) + ")")
    url = REPOSITORY + "/" + GROUP.replace(".", "/") + "/" + ARTIFACT + "/" + version + "/" + names[0]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
            output.write("Published and SHA-256 verified: [" + names[0] + "](" + url + ")\n\nSHA-256: `" + digest(directory / names[0]) + "`\n")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check-tag", "bundle", "verify", "restore", "preflight", "verify-published"])
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    if args.command == "check-tag":
        result = check_tag()
    elif args.command == "bundle":
        result = bundle(args.release)[0]
    elif args.command == "verify":
        result = verify_bundle(args.release)[0]
    elif args.command == "restore":
        result, directory, names = verify_bundle(True)
        (ROOT / "build/libs").mkdir(parents=True, exist_ok=True)
        for name in names[:2]:
            shutil.copyfile(directory / name, ROOT / "build/libs" / name)
    elif args.command == "preflight":
        result = preflight()
    else:
        result = verify_published()
    print(args.command + " OK: " + result)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, struct.error, zipfile.BadZipFile, ET.ParseError, subprocess.CalledProcessError) as error:
        print("Release validation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
