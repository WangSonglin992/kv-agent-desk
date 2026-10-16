"""Publish only the two public executable asset descriptors used by updates."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib


REPOSITORY = "WangSonglin992/kv-agent-desk"
EXECUTABLE = "KV-Agent-Desk.exe"


def write_manifest(executable: Path, output: Path, version: str) -> dict:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("version must be a stable X.Y.Z")
    executable, output = Path(executable), Path(output)
    checksum = executable.with_name(EXECUTABLE + ".sha256")
    if executable.name != EXECUTABLE or not executable.is_file() or not checksum.is_file():
        raise ValueError("public executable and checksum are required")
    with executable.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    line = checksum.read_text(encoding="ascii").strip()
    if line != f"{digest}  {EXECUTABLE}":
        raise ValueError("executable checksum mismatch")
    if not 0 < executable.stat().st_size <= 200 * 1024 * 1024 or checksum.stat().st_size > 1024:
        raise ValueError("invalid public asset size")
    tag = "v" + version
    payload = {"tag_name": tag, "draft": False, "prerelease": False, "assets": [
        {"name": file.name, "size": file.stat().st_size,
         "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{file.name}"}
        for file in (executable, checksum)]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("dist") / EXECUTABLE)
    parser.add_argument("--output", type=Path, default=Path("dist/KV-Agent-Desk.update.json"))
    parser.add_argument("--version")
    args = parser.parse_args()
    version = args.version
    if version is None:
        metadata = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        version = metadata["project"]["version"]
    write_manifest(args.exe, args.output, version)


if __name__ == "__main__":
    main()
