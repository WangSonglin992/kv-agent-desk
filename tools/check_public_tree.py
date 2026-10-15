"""Check the exact Git index before publishing; never read local account stores.

Reports include file/line/category only, never matching credential text.
This is a release guard, not a guarantee that all private data is detectable.
"""
from pathlib import PurePosixPath
import re
import subprocess
import sys


ROOT_FILES = {
    ".gitignore", ".gitattributes", "README.md", "LICENSE", "CHANGELOG.md",
    "THIRD_PARTY_NOTICES.md", "pyproject.toml", "requirements.txt",
    "requirements-build.lock.txt", "Build-Dashboard.ps1", "Start-Dashboard.cmd",
    "Stop-Dashboard.cmd", "run_dashboard.py",
}
ASSETS = {"app.ico", "deepseek.png", "deepseek.ico", "deepseek.svg", "SOURCES.md"}
BINARY_FILES = {"agent_dashboard/assets/" + name for name in ASSETS if name.endswith((".ico", ".png"))}
BINARY_FILES.add("docs/images/dashboard-demo.png")
PATTERNS = {
    "API key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    "GLM credential": re.compile(rb"\b[0-9a-fA-F]{32}\.[A-Za-z0-9]{16,}\b"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "private network": re.compile(rb"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    "local user path": re.compile(rb"[A-Za-z]:[/\\]+Users[/\\]+(?!Public\b|Default\b)[^/\\\s\"']+", re.I),
}


def allowed(name):
    path = PurePosixPath(name)
    if name in ROOT_FILES or name in BINARY_FILES:
        return True
    if name.startswith("agent_dashboard/assets/"):
        return len(path.parts) == 3 and path.name in ASSETS
    if name.startswith(("agent_dashboard/", "tests/", "tools/")):
        return path.suffix == ".py" and "__pycache__" not in path.parts
    if name.startswith("docs/"):
        return path.suffix == ".md"
    return name == ".github/workflows/windows-release.yml"


def main():
    rows = subprocess.check_output(["git", "ls-files", "--stage", "-z"]).split(b"\0")
    failures = []
    count = 0
    for row in rows:
        if not row:
            continue
        info, raw_name = row.split(b"\t", 1)
        mode, oid, stage = info.split()
        name = raw_name.decode("utf-8")
        count += 1
        if mode not in {b"100644", b"100755"} or stage != b"0" or not allowed(name):
            failures.append(f"{name}: file outside public allowlist")
            continue
        blob = subprocess.check_output(["git", "cat-file", "blob", oid.decode("ascii")])
        if len(blob) > 1024 * 1024:
            failures.append(f"{name}: unexpected file larger than 1 MiB")
        if name in BINARY_FILES:
            continue
        for category, pattern in PATTERNS.items():
            for match in pattern.finditer(blob):
                line = blob.count(b"\n", 0, match.start()) + 1
                failures.append(f"{name}:{line}: {category}")
    for failure in failures:
        print(failure)
    if not count:
        print("No staged/tracked files found")
    print(f"Public index: {count} files, {len(failures)} findings")
    return int(bool(failures) or not count)


if __name__ == "__main__":
    sys.exit(main())
