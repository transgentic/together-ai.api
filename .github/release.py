"""Publish the canonical recipe asset for this push, recovering incomplete drafts."""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def version(value):
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError("Expected a stable semantic version")
    return tuple(map(int, value.split(".")))


def reserve_tag(sha, requested):
    for _ in range(20):
        # Remote ref creation arbitrates concurrent pushes without dropping queued releases.
        refs = run("git", "ls-remote", "--tags", "--refs", "origin").splitlines()
        versions = []
        for ref in refs:
            commit, name = ref.split()
            tag = name.removeprefix("refs/tags/")
            if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
                continue
            if commit == sha:
                return tag
            versions.append(version(tag[1:]))
        candidate = version(requested)
        if versions and candidate <= max(versions):
            major, minor, patch = max(versions)
            candidate = (major, minor, patch + 1)
        tag = "v" + ".".join(map(str, candidate))
        push = subprocess.run(["git", "push", "origin", f"{sha}:refs/tags/{tag}"])
        if push.returncode == 0:
            return tag
    raise RuntimeError("Could not reserve a release tag")


def main():
    name = os.environ["GITHUB_REPOSITORY"].split("/")[1]
    filename = name + ".recipe.json"
    data = json.loads(Path(filename).read_text())
    slug, kind = name.split(".")
    provider_id = slug if kind == "webview" else kind + "_" + slug.replace("-", "_")
    if (data["repository"], data["providerType"], data["schemaVersion"], data["id"]) != (name, kind, 1, provider_id):
        raise ValueError("Recipe identity mismatch")
    if not data["title"].strip():
        raise ValueError("Missing provider title")
    if kind == "webview" and not (data["selectors"] and data["response"]["modes"]["text"]):
        raise ValueError("Missing WebView controls")
    if kind == "api" and not data["protocol"]:
        raise ValueError("Missing API protocol")
    if kind == "cli" and data["adapter"] != provider_id:
        raise ValueError("Invalid CLI adapter")
    version(data["version"])
    tag = reserve_tag(os.environ["GITHUB_SHA"], data["version"])
    releases = json.loads(run("gh", "api", "--paginate", "--slurp", "repos/{owner}/{repo}/releases?per_page=100"))
    release = next((r for page in releases for r in page if r["tag_name"] == tag), None)
    if release and not release["draft"]:
        if any(a["name"] == filename for a in release["assets"]):
            return
        raise RuntimeError("Published release is missing its asset")
    if not release:
        run("gh", "release", "create", tag, "--verify-tag", "--draft", "--title", tag, "--notes", data["title"])
    data["version"] = tag[1:]
    with tempfile.TemporaryDirectory() as directory:
        asset = Path(directory, filename)
        asset.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        run("gh", "release", "upload", tag, str(asset), "--clobber")
    run("gh", "release", "edit", tag, "--draft=false")


if __name__ == "__main__":
    main()
