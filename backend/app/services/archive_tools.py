from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[3]
BSDTAR_BIN = "/usr/bin/bsdtar"
EXTRACTOR_ENV = "PANELSTACK_ARCHIVE_EXTRACTOR"


class ArchiveToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveTool:
    kind: str
    path: str


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    size_bytes: int | None = None


def _tool_kind(path: str) -> str | None:
    name = Path(path).name.lower()
    if name == "bsdtar":
        return "bsdtar"
    if name in {"7zz", "7z", "7za"}:
        return "sevenzip"
    return None


def _candidate_paths() -> list[str]:
    candidates: list[str] = []
    env_path = os.getenv(EXTRACTOR_ENV)
    if env_path:
        candidates.append(env_path)
    candidates.extend(
        [
            BSDTAR_BIN,
            str(REPO_ROOT / "bin" / "7zz"),
            str(REPO_ROOT / "bin" / "7z"),
        ]
    )
    for executable in ("bsdtar", "7zz", "7z", "7za"):
        resolved = shutil.which(executable)
        if resolved:
            candidates.append(resolved)
    return candidates


def find_rar_tool() -> ArchiveTool | None:
    seen: set[str] = set()
    for candidate in _candidate_paths():
        if candidate in seen:
            continue
        seen.add(candidate)
        kind = _tool_kind(candidate)
        if kind is None:
            continue
        if Path(candidate).exists() or shutil.which(candidate):
            return ArchiveTool(kind=kind, path=candidate)
    return None


def rar_support_available() -> bool:
    return find_rar_tool() is not None


def _run_archive_command(command: list[str], *, text: bool) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=text)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ArchiveToolError(f"Unable to run archive extractor {' '.join(command[:2])}: {exc}") from exc


def _list_with_bsdtar(tool: ArchiveTool, archive_path: Path) -> list[ArchiveMember]:
    result = _run_archive_command([tool.path, "-tf", str(archive_path)], text=True)
    stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else result.stdout
    return [
        ArchiveMember(path=name.strip())
        for name in stdout.splitlines()
        if name.strip() and not name.strip().endswith("/") and not Path(name.strip()).name.startswith(".")
    ]


def _parse_7z_slt(stdout: str) -> list[ArchiveMember]:
    members: list[ArchiveMember] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if not current:
            return
        path = current.get("Path", "").strip()
        is_folder = current.get("Folder", "").strip() == "+"
        if path and not is_folder and not Path(path).name.startswith("."):
            raw_size = current.get("Size")
            try:
                size_bytes = int(raw_size) if raw_size is not None and raw_size.strip() else None
            except ValueError:
                size_bytes = None
            members.append(ArchiveMember(path=Path(path).as_posix(), size_bytes=size_bytes))

    for line in stdout.splitlines():
        if not line.strip():
            flush()
            current = {}
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        current[key.strip()] = value
    flush()
    return members


def _list_with_7z(tool: ArchiveTool, archive_path: Path) -> list[ArchiveMember]:
    result = _run_archive_command([tool.path, "l", "-slt", "-y", str(archive_path)], text=True)
    stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else result.stdout
    return _parse_7z_slt(stdout)


def list_rar_members(archive_path: Path) -> list[ArchiveMember]:
    tool = find_rar_tool()
    if tool is None:
        raise ArchiveToolError("No RAR/CBR extractor is available.")
    if tool.kind == "bsdtar":
        return _list_with_bsdtar(tool, archive_path)
    if tool.kind == "sevenzip":
        return _list_with_7z(tool, archive_path)
    raise ArchiveToolError(f"Unsupported archive extractor: {tool.path}")


def extract_rar_member_bytes(archive_path: Path, member_path: str) -> bytes:
    tool = find_rar_tool()
    if tool is None:
        raise ArchiveToolError("No RAR/CBR extractor is available.")
    if tool.kind == "bsdtar":
        command = [tool.path, "-xOf", str(archive_path), member_path]
    elif tool.kind == "sevenzip":
        command = [tool.path, "x", "-so", "-y", str(archive_path), member_path]
    else:
        raise ArchiveToolError(f"Unsupported archive extractor: {tool.path}")

    try:
        result = subprocess.run(command, check=False, capture_output=True)
    except FileNotFoundError as exc:
        raise ArchiveToolError(f"Unable to run archive extractor {tool.path}: {exc}") from exc
    if result.returncode != 0:
        error_message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ArchiveToolError(error_message or f"Unable to extract {member_path} from {archive_path}")
    return result.stdout
