"""Assemble the portable Windows release ZIP from an explicit public allowlist."""

from __future__ import annotations

import argparse
from importlib import metadata
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


PUBLIC_FILES = (
    ("README.md", "README.md"),
    ("LICENSE", "LICENSE"),
    ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
    ("agent_dashboard/assets/SOURCES.md", "assets/SOURCES.md"),
    ("agent_dashboard/assets/chatgpt.png", "assets/chatgpt.png"),
    ("agent_dashboard/assets/glm.png", "assets/glm.png"),
    ("agent_dashboard/assets/google.png", "assets/google.png"),
    ("docs/RELEASING.md", "docs/RELEASING.md"),
    ("docs/PRIVACY.md", "docs/PRIVACY.md"),
    ("docs/EXTENDING.md", "docs/EXTENDING.md"),
    ("docs/UI-TEMPLATES.md", "docs/UI-TEMPLATES.md"),
    ("docs/images/dashboard-demo.png", "docs/images/dashboard-demo.png"),
)
LOCK_FILE = "requirements-build.lock.txt"
CPYTHON_LICENSE_CANDIDATES = ("LICENSE.txt", "LICENSE")
LICENSE_MARKERS = ("license", "licence", "copying", "copyright", "notice")
MAX_LICENSE_BYTES = 4 * 1024 * 1024
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PackageError(RuntimeError):
    """A required, public release input is missing or unsafe to archive."""


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _required_regular_file(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PackageError(f"required {label} is missing: {path.name}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PackageError(f"required {label} must be a regular file: {path.name}")
    if info.st_size == 0:
        raise PackageError(f"required {label} is empty: {path.name}")
    return path


def _safe_arcname(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in normalized:
        raise PackageError(f"unsafe ZIP entry path: {name!r}")
    return path.as_posix()


def _locked_package_names(lock_path: Path) -> set[str]:
    try:
        lines = lock_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PackageError(f"build dependency lock is missing: {lock_path.name}") from exc

    names: set[str] = set()
    for line in lines:
        requirement = line.partition("#")[0].strip()
        if not requirement or requirement.startswith(("-", ".")):
            continue
        name = re.split(r"===|==|~=|!=|<=|>=|<|>|;", requirement, maxsplit=1)[0].strip()
        if PACKAGE_NAME_RE.fullmatch(name):
            names.add(_normalize_name(name))
    if not names:
        raise PackageError(f"no pinned package names found in {lock_path.name}")
    return names


def _is_license_path(path: PurePosixPath) -> bool:
    return any(marker in part.casefold() for part in path.parts for marker in LICENSE_MARKERS)


def _is_text_license(path: Path) -> bool:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_size > MAX_LICENSE_BYTES:
            return False
        data = path.read_bytes()
    except OSError:
        return False
    if b"\0" in data:
        return False
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError:
            return False
    return not any(ord(char) < 32 and char not in "\t\n\r\f" for char in text)


def _dependency_license_files(lock_path: Path) -> list[tuple[Path, str]]:
    locked_names = _locked_package_names(lock_path)
    found_names: set[str] = set()
    license_files: list[tuple[Path, str]] = []
    seen_arc_names: set[str] = set()

    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name or _normalize_name(name) not in locked_names:
            continue
        normalized_name = _normalize_name(name)
        found_names.add(normalized_name)
        version = re.sub(r"[^A-Za-z0-9._+-]", "_", distribution.version)
        package_dir = f"{normalized_name}-{version}"
        for file in distribution.files or ():
            relative = PurePosixPath(str(file).replace("\\", "/"))
            if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                continue
            if not _is_license_path(relative):
                continue
            source = Path(distribution.locate_file(file))
            if not _is_text_license(source):
                continue
            arcname = _safe_arcname(
                PurePosixPath("licenses", "python", package_dir, *relative.parts).as_posix()
            )
            if arcname in seen_arc_names:
                continue
            seen_arc_names.add(arcname)
            license_files.append((source, arcname))

    missing = sorted(locked_names - found_names)
    if missing:
        raise PackageError(
            "locked build dependencies are not installed: " + ", ".join(missing)
        )
    return license_files


def _cpython_license() -> Path:
    prefix = Path(sys.base_prefix)
    for name in CPYTHON_LICENSE_CANDIDATES:
        candidate = prefix / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise PackageError("CPython license file not found beside the active Python installation")


def package_release(
    exe_path: Path | str,
    output_path: Path | str,
    *,
    project_root: Path | str | None = None,
) -> tuple[Path, int]:
    """Create a portable ZIP containing only the executable and public notices."""
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
    root = root.resolve()
    exe = _required_regular_file(Path(exe_path), "PyInstaller executable")
    if exe.name != "KV-Agent-Desk.exe":
        raise PackageError("expected the clean CI executable named KV-Agent-Desk.exe")

    output = Path(output_path)
    if output.resolve() == exe.resolve():
        raise PackageError("ZIP output path must not overwrite the executable")

    sources: list[tuple[Path, str]] = [(exe, "KV-Agent-Desk.exe")]
    for relative, arcname in PUBLIC_FILES:
        source = _required_regular_file(root / Path(*PurePosixPath(relative).parts), relative)
        sources.append((source, _safe_arcname(arcname)))

    cpython_license = _required_regular_file(_cpython_license(), "CPython license")
    sources.append((cpython_license, "licenses/CPython/LICENSE.txt"))

    dependency_licenses = _dependency_license_files(root / LOCK_FILE)
    sources.extend(dependency_licenses)

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=output.name + ".", suffix=".tmp", dir=output.parent, delete=False
        ) as temporary:
            temp_name = temporary.name
        with ZipFile(temp_name, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            seen: set[str] = set()
            for source, arcname in sources:
                safe_name = _safe_arcname(arcname)
                if safe_name in seen:
                    raise PackageError(f"duplicate ZIP entry: {safe_name}")
                seen.add(safe_name)
                archive.write(source, arcname=safe_name, compress_type=ZIP_DEFLATED)
        os.replace(temp_name, output)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    return output, len(dependency_licenses)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Package a portable public Windows release ZIP")
    parser.add_argument("--exe", type=Path, default=root / "dist" / "KV-Agent-Desk.exe")
    parser.add_argument(
        "--output", type=Path, default=root / "dist" / "KV-Agent-Desk-windows-x64.zip"
    )
    parser.add_argument("--project-root", type=Path, default=root)
    args = parser.parse_args(argv)

    try:
        output, dependency_licenses = package_release(
            args.exe, args.output, project_root=args.project_root
        )
    except PackageError as exc:
        parser.exit(2, f"package_release: {exc}\n")
    print(f"Created {output.name}; bundled {dependency_licenses} dependency license text files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
