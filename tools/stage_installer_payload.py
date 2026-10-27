"""Stage the explicitly allowed runtime and license files for the Windows Setup."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import shutil

if __package__:
    from .package_release import (
        PackageError,
        _cpython_license,
        _dependency_license_files,
        _required_regular_file,
        _safe_arcname,
    )
else:  # Direct invocation as: python tools/stage_installer_payload.py
    from package_release import (
        PackageError,
        _cpython_license,
        _dependency_license_files,
        _required_regular_file,
        _safe_arcname,
    )


FIXED_FILES = (
    ("LICENSE", "LICENSE"),
    ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
)


def stage_setup_payload(
    exe_path: Path | str,
    output_dir: Path | str,
    *,
    project_root: Path | str | None = None,
) -> tuple[Path, int]:
    """Copy the EXE plus public notices and text licenses into a new directory.

    The destination must not already exist, so stale or user-created files can
    never accidentally become part of the Setup payload.
    """
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
    root = root.resolve()
    exe = _required_regular_file(Path(exe_path), "PyInstaller executable")
    if exe.name != "KV-Agent-Desk.exe":
        raise PackageError("expected the clean CI executable named KV-Agent-Desk.exe")

    output = Path(output_dir).resolve()
    if output.exists():
        raise PackageError("Setup payload destination must not already exist")

    sources: list[tuple[Path, str]] = [(exe, "KV-Agent-Desk.exe")]
    for relative, arcname in FIXED_FILES:
        source = _required_regular_file(root / relative, relative)
        sources.append((source, _safe_arcname(arcname)))

    cpython_license = _required_regular_file(_cpython_license(), "CPython license")
    sources.append((cpython_license, "licenses/CPython/LICENSE.txt"))
    dependency_licenses = _dependency_license_files(root / "requirements-build.lock.txt")
    sources.extend(dependency_licenses)

    output.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    try:
        for source, arcname in sources:
            safe_name = _safe_arcname(arcname)
            if safe_name in seen:
                raise PackageError(f"duplicate Setup payload path: {safe_name}")
            seen.add(safe_name)
            destination = output.joinpath(*PurePosixPath(safe_name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    except OSError as exc:
        raise PackageError("could not stage the Setup payload") from exc

    return output, len(dependency_licenses)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=root / "dist" / "KV-Agent-Desk.exe")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=root)
    args = parser.parse_args(argv)

    try:
        output, dependency_licenses = stage_setup_payload(
            args.exe, args.output_dir, project_root=args.project_root
        )
    except PackageError as exc:
        parser.exit(2, f"stage_installer_payload: {exc}\n")
    print(f"Staged Setup payload with {dependency_licenses} dependency license text files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
