#!/usr/bin/env python3
"""Install keel-sdk from a GitHub Release wheel.

This file is copied into generated projects and must run before keel-sdk itself
is installed, so it cannot import the keel package.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

RELEASE_LINE = re.compile(r"1\.\d+\.\d+")
RELEASE_TAG = re.compile(r"sdk-v(1\.\d+\.\d+)")
DEFAULT_VERSION = "1.0.0"
DEFAULT_REPO = "dezhiguan/keel"
USER_AGENT = "keel-sdk-installer"


def version_from_tag(tag: str) -> str:
    matched = RELEASE_TAG.fullmatch(tag or "")
    if matched is None:
        raise ValueError(f"tag must be sdk-v1.x.y, got {tag!r}")
    return matched.group(1)


def require_release_version(version: str) -> str:
    if RELEASE_LINE.fullmatch(version or "") is None:
        raise ValueError(f"SDK release version must be 1.x.y, got {version!r}")
    return version


def wheel_name(version: str) -> str:
    return f"keel_sdk-{require_release_version(version)}-py3-none-any.whl"


def pip_install_args(wheel: Path) -> list[str]:
    if wheel.name == "keel-sdk" or not wheel.name.endswith(".whl"):
        raise ValueError(f"refusing to install {wheel}; expected a wheel file")
    return [sys.executable, "-m", "pip", "install", "--no-cache-dir", str(wheel)]


def _request(url: str, token: str, accept: str) -> bytes:
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub returned {error.code} for {url}") from error


def download_wheel(api: str, repo: str, version: str, token: str, dest: Path) -> Path:
    version = require_release_version(version)
    if not token.strip():
        raise ValueError("KEEL_GITHUB_TOKEN is required")
    tag = f"sdk-v{version}"
    payload = json.loads(_request(
        f"{api.rstrip('/')}/repos/{repo}/releases/tags/{tag}",
        token,
        "application/vnd.github+json",
    ))
    expected = wheel_name(version)
    asset = next((item for item in payload.get("assets", []) if item.get("name") == expected), None)
    if asset is None or not asset.get("url"):
        raise RuntimeError(f"release {tag} has no {expected}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_request(asset["url"], token, "application/octet-stream"))
    return dest


def install_from_env() -> None:
    token = os.environ.get("KEEL_GITHUB_TOKEN", "")
    if not token.strip():
        raise SystemExit("KEEL_GITHUB_TOKEN is required to download the private keel-sdk wheel")
    version = require_release_version(os.environ.get("KEEL_SDK_VERSION", DEFAULT_VERSION))
    repo = os.environ.get("KEEL_GITHUB_REPO", DEFAULT_REPO).strip() or DEFAULT_REPO
    api = os.environ.get("KEEL_GITHUB_API", "https://api.github.com")
    with tempfile.TemporaryDirectory() as directory:
        wheel = download_wheel(api, repo, version, token, Path(directory) / wheel_name(version))
        subprocess.check_call(pip_install_args(wheel))


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--check-tag"]:
        if len(args) != 2:
            raise SystemExit("usage: install_keel_sdk.py --check-tag sdk-v1.x.y")
        print(version_from_tag(args[1]))
        return
    if args:
        raise SystemExit("usage: install_keel_sdk.py [--check-tag sdk-v1.x.y]")
    install_from_env()


if __name__ == "__main__":
    main()
