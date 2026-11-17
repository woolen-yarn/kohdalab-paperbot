"""Publish an already built and tested distribution to a new GitHub Release.

Runs inside the single GitHub Actions job that built and tested the files, so
nothing goes through upload-artifact. The flow is deliberately conservative:

1. Stage archives (.zip/.exe/.pdf/.whl/.tar.gz) and .json verification reports
   from one flat directory into a fresh build/distribution-stage.
2. Add a stable alias for the single portable archive, the source archive,
   BUILD-INFO.json and SHA256SUMS.txt.
3. Create a *draft* release on a new, unique build-<run>-<attempt> tag.
4. Compare the uploaded asset inventory and GitHub's SHA-256 digests with the
   staged files, and only then publish the release as latest.

An existing release or tag is never replaced. On any failure the draft is left
in place for inspection. Binaries are unsigned and hardware was not tested;
the release records a software-only qualification.

    python3 scripts/publish_distribution.py --directory dist \
        --portable-glob 'kohdalab-iv-*-windows-x64.zip' \
        --alias kohdalab-iv-windows-x64.zip
"""
from __future__ import annotations

import argparse
from fnmatch import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "build" / "distribution-stage"
ARCHIVE_SUFFIXES = (".zip", ".exe", ".pdf", ".whl", ".tar.gz")
REPORT_SUFFIX = ".json"
# Unsigned first-party binaries handed to code signing; never release assets.
SIGNING_INPUTS = {
    "kohdalab-iv.exe",
    "iv-cli.exe",
    "kohdalab-usbtmc-helper.exe",
    "kohdalab-visa-helper.exe",
    "USB-Setup.exe",
}
BUILD_INFO = "BUILD-INFO.json"
CHECKSUMS = "SHA256SUMS.txt"
REQUIRED_ENV = (
    "GITHUB_REPOSITORY",
    "GITHUB_SHA",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_NUMBER",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_SERVER_URL",
    "GITHUB_REF_NAME",
    "GITHUB_REF",
)
QUALIFICATION = (
    "Software-only qualification: built and tested by GitHub Actions. "
    "Binaries are unsigned. Hardware was not tested."
)


class PublishError(RuntimeError):
    pass


# --- deterministic helpers ---------------------------------------------------

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_stageable(name: str) -> bool:
    if name in SIGNING_INPUTS:
        return False
    return name.endswith(ARCHIVE_SUFFIXES) or name.endswith(REPORT_SUFFIX)


def archive_suffix(name: str) -> str:
    for suffix in ARCHIVE_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return ""


def plain_filename(name: str, label: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise PublishError(f"{label} must be a plain file name: {name!r}")
    return name


def read_environment(environ: dict[str, str]) -> dict[str, str]:
    missing = [key for key in REQUIRED_ENV if not environ.get(key)]
    if missing:
        raise PublishError("Missing environment: " + ", ".join(missing))
    env = {key: environ[key] for key in REQUIRED_ENV}
    branch = environ.get("DISTRIBUTION_BRANCH") or "main"
    if env["GITHUB_REF"] != f"refs/heads/{branch}":
        raise PublishError(
            f"Refusing to publish from {env['GITHUB_REF_NAME']!r}; only {branch!r} publishes"
        )
    if "/" not in env["GITHUB_REPOSITORY"]:
        raise PublishError("GITHUB_REPOSITORY must be owner/name")
    return env


def cargo_version(root: Path) -> str | None:
    manifest = root / "Cargo.toml"
    if not manifest.is_file():
        return None
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    for table in (data.get("package", {}), data.get("workspace", {}).get("package", {})):
        version = table.get("version")
        if isinstance(version, str):
            return version
    return None


def release_tag(env: dict[str, str]) -> str:
    return f"build-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}"


def stage_directory(directory: Path, stage: Path, reserved: set[str]) -> list[str]:
    """Copy stageable files from a flat directory into a fresh stage."""
    if not directory.is_dir():
        raise PublishError(f"Not a directory: {directory}")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    staged = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or not is_stageable(path.name):
            continue
        if path.name in reserved:
            raise PublishError(f"Input file collides with a generated asset: {path.name}")
        shutil.copyfile(path, stage / path.name)
        staged.append(path.name)
    if not staged:
        raise PublishError(f"No distribution files found in {directory}")
    return staged


def single_portable(names: list[str], pattern: str) -> str:
    matches = [name for name in names if archive_suffix(name) and fnmatch(name, pattern)]
    if len(matches) != 1:
        raise PublishError(f"Expected exactly one portable matching {pattern!r}, found {matches}")
    return matches[0]


def build_info(env: dict[str, str], version: str | None, tag: str, portable: str,
               alias: str, source: str) -> dict:
    repository = env["GITHUB_REPOSITORY"]
    server = env["GITHUB_SERVER_URL"].rstrip("/")
    return {
        "repository": repository,
        "source_sha": env["GITHUB_SHA"],
        "branch": env["GITHUB_REF_NAME"],
        "version": version,
        "tag": tag,
        "build": {
            "run_id": env["GITHUB_RUN_ID"],
            "run_number": env["GITHUB_RUN_NUMBER"],
            "run_attempt": env["GITHUB_RUN_ATTEMPT"],
            "run_url": f"{server}/{repository}/actions/runs/{env['GITHUB_RUN_ID']}",
        },
        "portable": portable,
        "portable_alias": alias,
        "source_archive": source,
        "signing": "unsigned",
        "hardware": "not tested",
        "qualification": os.environ.get("DISTRIBUTION_QUALIFICATION", "software-only"),
    }


def write_checksums(stage: Path) -> dict[str, str]:
    digests = {
        path.name: sha256(path)
        for path in sorted(stage.iterdir())
        if path.is_file() and path.name != CHECKSUMS
    }
    (stage / CHECKSUMS).write_text(
        "".join(f"{digest}  {name}\n" for name, digest in digests.items()), encoding="utf-8"
    )
    digests[CHECKSUMS] = sha256(stage / CHECKSUMS)
    return digests


def inventory_problems(expected: dict[str, tuple[int, str]], assets: list[dict]) -> list[str]:
    """Compare uploaded assets ({name, size, digest}) with local (size, sha256)."""
    problems = []
    remote = {}
    for asset in assets:
        name = asset.get("name")
        if name in remote:
            problems.append(f"duplicate asset {name}")
        remote[name] = asset
    for name in sorted(set(expected) - set(remote)):
        problems.append(f"missing asset {name}")
    for name in sorted(set(remote) - set(expected), key=str):
        problems.append(f"unexpected asset {name}")
    for name in sorted(set(expected) & set(remote)):
        size, digest = expected[name]
        asset = remote[name]
        if asset.get("size") != size:
            problems.append(f"size mismatch {name}: {asset.get('size')} != {size}")
        if asset.get("digest") != f"sha256:{digest}":
            problems.append(f"digest mismatch {name}: {asset.get('digest')} != sha256:{digest}")
    return problems


def release_notes(info: dict, digests: dict[str, str]) -> str:
    lines = [
        f"Automated build of `{info['repository']}` at `{info['source_sha']}`"
        + (f" (version {info['version']})." if info["version"] else "."),
        "",
        f"- Workflow run: {info['build']['run_url']}",
        f"- Portable: `{info['portable_alias']}` (copy of `{info['portable']}`)",
        f"- Source: `{info['source_archive']}`",
        "",
        f"**{QUALIFICATION}**",
        "",
        QUALIFICATION + " " + info["qualification"],
        "",
        "| File | SHA-256 |",
        "| --- | --- |",
    ]
    lines += [f"| `{name}` | `{digest}` |" for name, digest in sorted(digests.items())]
    return "\n".join(lines) + "\n"


# --- side effects -------------------------------------------------------------

def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise PublishError(f"{command[0]} {command[1]} failed: {result.stderr.strip()}")
    return result


def gh_exists(api_path: str) -> bool:
    """True if the GitHub API object exists, False on 404; anything else fails."""
    result = run(["gh", "api", api_path], check=False)
    if result.returncode == 0:
        return True
    if "HTTP 404" in result.stderr:
        return False
    raise PublishError(f"gh api {api_path} failed: {result.stderr.strip()}")


def refuse_existing(repository: str, tag: str) -> None:
    tags = run(["gh", "api", "--paginate", f"repos/{repository}/releases",
                "--jq", ".[].tag_name"]).stdout.splitlines()
    if tag in tags:
        raise PublishError(f"Release {tag} already exists; refusing to replace it")
    if gh_exists(f"repos/{repository}/releases/tags/{tag}"):
        raise PublishError(f"Release {tag} already exists; refusing to replace it")
    if gh_exists(f"repos/{repository}/git/ref/tags/{tag}"):
        raise PublishError(f"Tag {tag} already exists; refusing to replace it")


def view_release(repository: str, tag: str) -> dict:
    result = run(["gh", "release", "view", tag, "--repo", repository,
                  "--json", "assets,isDraft,tagName,targetCommitish,url"])
    return json.loads(result.stdout)


def write_summary(info: dict, url: str, digests: dict[str, str], server: str) -> None:
    base = f"{server}/{info['repository']}/releases/download/{info['tag']}"
    lines = [
        f"## Distribution {info['tag']}",
        "",
        f"Release: {url}",
        "",
        QUALIFICATION + " " + info["qualification"],
        "",
        "| File | SHA-256 |",
        "| --- | --- |",
    ]
    lines += [f"| [{name}]({base}/{name}) | `{digest}` |" for name, digest in sorted(digests.items())]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"tag={info['tag']}\nurl={url}\n")
    print("\n".join(lines))


def publish(arguments: argparse.Namespace) -> None:
    env = read_environment(dict(os.environ))
    repository, sha = env["GITHUB_REPOSITORY"], env["GITHUB_SHA"]
    server = env["GITHUB_SERVER_URL"].rstrip("/")
    tag = release_tag(env)

    alias = plain_filename(arguments.alias, "--alias")
    source = plain_filename(
        arguments.source_alias or f"{repository.split('/')[1]}-build-source.zip", "--source-alias"
    )
    generated = {alias, source, BUILD_INFO, CHECKSUMS}
    if len(generated) != 4:
        raise PublishError("--alias and source archive names must be distinct")

    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if head != sha:
        raise PublishError(f"Checked-out HEAD {head} is not GITHUB_SHA {sha}")
    refuse_existing(repository, tag)

    staged = stage_directory(arguments.directory.resolve(), STAGE, {source, BUILD_INFO, CHECKSUMS})
    portable = single_portable(staged, arguments.portable_glob)
    if archive_suffix(alias) != archive_suffix(portable):
        raise PublishError(f"--alias {alias} must keep the {archive_suffix(portable)} suffix")
    if portable != alias:
        if alias in staged:
            raise PublishError(f"Alias collides with an input file: {alias}")
        shutil.copyfile(STAGE / portable, STAGE / alias)
    if arguments.source_alias:
        # Source-only repositories: the tested portable already is the source.
        if archive_suffix(source) != archive_suffix(portable):
            raise PublishError(f"--source-alias must keep the {archive_suffix(portable)} suffix")
        shutil.copyfile(STAGE / portable, STAGE / source)
    else:
        prefix = f"{repository.split('/')[1]}-{sha[:12]}/"
        run(["git", "archive", "--format=zip", f"--prefix={prefix}",
             "-o", str(STAGE / source), sha])

    info = build_info(env, cargo_version(ROOT), tag, portable, alias, source)
    (STAGE / BUILD_INFO).write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    digests = write_checksums(STAGE)
    expected = {name: ((STAGE / name).stat().st_size, digest) for name, digest in digests.items()}

    notes = STAGE.parent / "distribution-release-notes.md"
    notes.write_text(release_notes(info, digests), encoding="utf-8")
    title = f"{repository.split('/')[1]} {info['version'] or sha[:12]} ({tag})"
    # Creating the ref is atomic: a concurrent publisher cannot replace this tag.
    ref_body = STAGE.parent / "distribution-tag.json"
    ref_body.write_text(json.dumps({"ref": f"refs/tags/{tag}", "sha": sha}), encoding="utf-8")
    run(["gh", "api", "--method", "POST", f"repos/{repository}/git/refs",
         "--input", str(ref_body)])
    tagged = json.loads(run(["gh", "api", f"repos/{repository}/commits/{tag}"]).stdout)["sha"]
    if tagged != sha:
        raise PublishError("New tag does not resolve to the tested commit")

    run(["gh", "release", "create", tag, "--repo", repository, "--draft", "--verify-tag",
         "--target", sha, "--title", title, "--notes-file", str(notes),
         *[str(STAGE / name) for name in sorted(digests)]])
    print(f"Created draft release {tag}; verifying uploaded assets")

    draft = view_release(repository, tag)
    if not draft.get("isDraft") or draft.get("targetCommitish") != sha:
        raise PublishError(f"Release {tag} is not a draft targeting {sha}")
    problems = inventory_problems(expected, draft.get("assets", []))
    if problems:
        raise PublishError("Uploaded assets do not match staged files:\n  " + "\n  ".join(problems))

    try:
        run(["gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest"])
        final = view_release(repository, tag)
        if final.get("isDraft") or final.get("targetCommitish") != sha:
            raise PublishError("Published release identity differs from the verified draft")
        problems = inventory_problems(expected, final.get("assets", []))
        if problems:
            raise PublishError("Published files differ: " + ", ".join(problems))
        tagged = json.loads(run(["gh", "api", f"repos/{repository}/commits/{tag}"]).stdout)["sha"]
        if tagged != sha:
            raise PublishError(f"Published tag {tag} points to {tagged}, expected {sha}")
    except Exception:
        run(["gh", "release", "edit", tag, "--repo", repository, "--draft=true"], check=False)
        raise
    write_summary(info, final["url"], digests, server)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--directory", type=Path, required=True,
                        help="flat directory holding built and tested files")
    parser.add_argument("--portable-glob", required=True,
                        help="pattern matching exactly one portable archive")
    parser.add_argument("--alias", required=True,
                        help="stable file name for a copy of the portable archive")
    parser.add_argument("--source-alias",
                        help="publish the portable as this source archive instead of git archive")
    try:
        publish(parser.parse_args())
    except PublishError as error:
        print(f"error: {error}", file=sys.stderr)
        print("Any draft release created by this run was left in place for inspection.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
