from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import re
import threading
import zipfile
from typing import Iterable, Sequence

from .archive_tools import ArchiveToolError, list_rar_members


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"}
ARCHIVE_EXTENSIONS = {".cbz", ".zip", ".cbr", ".rar", ".pdf"}
KNOWN_MARKER_TOKENS = {
    "digital",
    "cbz",
    "c2c",
    "comic",
    "comics",
    "scan",
    "scans",
}


class IngestError(RuntimeError):
    pass


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


@dataclass(frozen=True)
class ComicMetadata:
    raw_name: str
    title: str
    series: str | None = None
    issue: str | None = None
    volume: str | None = None
    issue_kind: str = "issue"
    year: int | None = None
    publisher: str | None = None
    release_group: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class PageRecord:
    index: int
    relative_path: str
    size_bytes: int | None
    extension: str


@dataclass(frozen=True)
class ScanResult:
    source_path: str
    source_kind: str
    archive_format: str | None
    page_count: int
    file_count: int
    total_bytes: int
    metadata: ComicMetadata
    pages: tuple[PageRecord, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["pages"] = [dict(page) for page in payload["pages"]]
        payload["warnings"] = list(payload["warnings"])
        return payload


@dataclass(frozen=True)
class IngestJobRequest:
    paths: tuple[str, ...]
    recursive: bool = True


@dataclass
class IngestJobRecord:
    job_id: str
    request: IngestJobRequest
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    scans: tuple[ScanResult, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "request": {
                "paths": list(self.request.paths),
                "recursive": self.request.recursive,
            },
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "scans": [scan.to_dict() for scan in self.scans],
            "error": self.error,
        }


def _natural_key(value: str) -> list[object]:
    parts = re.split(r"(\d+)", value.lower())
    key: list[object] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        elif part:
            key.append(part)
    return key


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _is_archive(path: Path) -> bool:
    return path.suffix.lower() in ARCHIVE_EXTENSIONS


def _has_direct_image_children(path: Path) -> bool:
    return any(child.is_file() and _is_image(child) and not _is_hidden(child) for child in path.iterdir())


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _sanitize_segment(segment: str) -> str:
    cleaned = _normalize_spaces(re.sub(r"[_\.]+", " ", segment))
    cleaned = cleaned.strip(" -_")
    return cleaned


def _maybe_release_group(segment: str) -> bool:
    lowered = segment.strip().lower()
    if not lowered:
        return False
    if lowered in KNOWN_MARKER_TOKENS:
        return False
    if re.fullmatch(r"19\d{2}|20\d{2}", lowered):
        return False
    if not re.search(r"[A-Za-z]", segment):
        return False
    if "vol" in lowered or "issue" in lowered or lowered.startswith("#"):
        return False
    return True


def infer_metadata_from_name(raw_name: str) -> ComicMetadata:
    stem = Path(raw_name).stem
    candidate = _sanitize_segment(stem)
    parenthetical = [segment.strip() for segment in re.findall(r"\(([^()]*)\)", candidate)]
    release_group = None
    year = None
    for segment in parenthetical:
        match = re.fullmatch(r"(19\d{2}|20\d{2})", segment)
        if match:
            year = int(match.group(1))
            break
    for segment in reversed(parenthetical):
        if _maybe_release_group(segment):
            release_group = segment
            break

    stripped = re.sub(r"\([^()]*\)", " ", candidate)
    stripped = _normalize_spaces(stripped)

    volume_match = re.search(r"\b(?:vol(?:ume)?\.?|v)\s*(\d+)\b", stripped, flags=re.I)
    issue_source = stripped
    if volume_match:
        issue_source = re.sub(r"\b(?:vol(?:ume)?\.?|v)\s*\d+\b", " ", issue_source, flags=re.I)
        issue_source = _normalize_spaces(issue_source)
    issue_match = re.search(
        r"(?:#\s*|issue\s*)?(\d{1,4}(?:\.\d+)?(?:\s*-\s*\d{1,4}(?:\.\d+)?)?)\b",
        issue_source,
        flags=re.I,
    )

    title = stripped
    confidence = 0.35

    if issue_match:
        issue = issue_match.group(1).replace(" ", "")
        title = issue_source[: issue_match.start()].strip(" -_#")
        confidence += 0.35
    else:
        issue = None

    if volume_match:
        volume = volume_match.group(1)
        title = re.sub(r"\b(?:vol(?:ume)?\.?|v)\s*\d+\b", "", title, flags=re.I)
        confidence += 0.15
    else:
        volume = None

    title = _normalize_spaces(re.sub(r"\s*[\(\[][^()\[\]]*[\)\]]\s*", " ", title))
    title = _normalize_spaces(title or stripped or stem)

    issue_kind = "issue"
    annual_match = re.search(r"\bannual\b", stripped, flags=re.I)
    one_shot_match = re.search(r"\bone[\s-]?shot\b", stripped, flags=re.I)
    special_match = re.search(r"\b(?:special|giant[\s-]?size)\b", stripped, flags=re.I)
    collection_match = re.search(
        r"\b(?:tpb|trade paperback|hc|hardcover|omnibus|deluxe(?: edition)?|complete collection|collection)\b",
        stripped,
        flags=re.I,
    )

    if annual_match:
        issue_kind = "annual"
        title = _normalize_spaces(re.sub(r"\bannual\b", " ", title, flags=re.I))
        if not issue and year:
            issue = str(year)
    elif collection_match or (issue and "-" in issue):
        issue_kind = "collection"
        title = _normalize_spaces(
            re.sub(
                r"\b(?:tpb|trade paperback|hc|hardcover|omnibus|deluxe(?: edition)?|complete collection|collection)\b",
                " ",
                title,
                flags=re.I,
            )
        )
    elif one_shot_match:
        issue_kind = "one-shot"
        title = _normalize_spaces(re.sub(r"\bone[\s-]?shot\b", " ", title, flags=re.I))
    elif special_match:
        issue_kind = "special"
        title = _normalize_spaces(re.sub(r"\b(?:special|giant[\s-]?size)\b", " ", title, flags=re.I))

    series = title if title else None
    publisher = None
    lower_title = title.lower()
    if lower_title.startswith("marvel "):
        publisher = "Marvel"
    elif lower_title.startswith("dc "):
        publisher = "DC"

    return ComicMetadata(
        raw_name=raw_name,
        title=title,
        series=series,
        issue=issue,
        volume=volume,
        issue_kind=issue_kind,
        year=year,
        publisher=publisher,
        release_group=release_group,
        confidence=min(confidence, 0.99),
    )


def _collect_directory_pages(path: Path, recursive: bool) -> tuple[PageRecord, ...]:
    if recursive:
        candidates = [candidate for candidate in path.rglob("*") if candidate.is_file() and not _is_hidden(candidate)]
    else:
        candidates = [candidate for candidate in path.iterdir() if candidate.is_file() and not _is_hidden(candidate)]

    pages: list[PageRecord] = []
    for candidate in sorted((item for item in candidates if _is_image(item)), key=lambda item: _natural_key(str(item.relative_to(path)))):
        relative_path = candidate.relative_to(path).as_posix()
        pages.append(
            PageRecord(
                index=len(pages) + 1,
                relative_path=relative_path,
                size_bytes=candidate.stat().st_size,
                extension=candidate.suffix.lower(),
            )
        )
    return tuple(pages)


def _collect_zip_pages(path: Path) -> tuple[PageRecord, ...]:
    pages: list[PageRecord] = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/") and not Path(name).name.startswith(".")]
        for name in sorted((name for name in names if Path(name).suffix.lower() in IMAGE_EXTENSIONS), key=_natural_key):
            info = archive.getinfo(name)
            pages.append(
                PageRecord(
                    index=len(pages) + 1,
                    relative_path=Path(name).as_posix(),
                    size_bytes=info.file_size,
                    extension=Path(name).suffix.lower(),
                )
            )
    return tuple(pages)


def _collect_rar_pages(path: Path) -> tuple[PageRecord, ...]:
    try:
        members = list_rar_members(path)
    except ArchiveToolError as exc:
        raise IngestError(f"Unable to inspect RAR/CBR archive {path}: {exc}") from exc

    pages: list[PageRecord] = []
    image_members = [member for member in members if Path(member.path).suffix.lower() in IMAGE_EXTENSIONS]
    for member in sorted(image_members, key=lambda item: _natural_key(item.path)):
        pages.append(
            PageRecord(
                index=len(pages) + 1,
                relative_path=Path(member.path).as_posix(),
                size_bytes=member.size_bytes,
                extension=Path(member.path).suffix.lower(),
            )
        )
    return tuple(pages)


def _count_pdf_pages(path: Path) -> tuple[int, str | None]:
    def heuristic_count() -> tuple[int, str | None]:
        data = path.read_bytes()
        matches = re.findall(rb"/Type\s*/Page\b", data)
        count = len(matches)
        if count == 0:
            count = 1 if data else 0
        return count, "pdf page count inferred heuristically without full rendering support"

    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return heuristic_count()

    try:
        reader = PdfReader(str(path))
    except Exception:
        return heuristic_count()
    return len(reader.pages), None


def scan_directory(path: Path, recursive: bool = True) -> ScanResult:
    pages = _collect_directory_pages(path, recursive)
    total_bytes = sum(page.size_bytes or 0 for page in pages)
    warnings: list[str] = []
    if not pages:
        warnings.append("No image pages were found in the directory.")
    return ScanResult(
        source_path=str(path),
        source_kind="directory",
        archive_format=None,
        page_count=len(pages),
        file_count=sum(1 for candidate in path.rglob("*") if candidate.is_file()) if recursive else sum(
            1 for candidate in path.iterdir() if candidate.is_file()
        ),
        total_bytes=total_bytes,
        metadata=infer_metadata_from_name(path.name),
        pages=pages,
        warnings=tuple(warnings),
    )


def scan_archive(path: Path) -> ScanResult:
    suffix = path.suffix.lower()
    warnings: list[str] = []

    if suffix in {".cbz", ".zip"}:
        pages = _collect_zip_pages(path)
        total_bytes = sum(page.size_bytes or 0 for page in pages)
        return ScanResult(
            source_path=str(path),
            source_kind="archive",
            archive_format="zip",
            page_count=len(pages),
            file_count=len(pages),
            total_bytes=total_bytes,
            metadata=infer_metadata_from_name(path.name),
            pages=pages,
            warnings=tuple(warnings),
        )

    if suffix == ".pdf":
        page_count, warning = _count_pdf_pages(path)
        if warning:
            warnings.append(warning)
        size_bytes = path.stat().st_size
        return ScanResult(
            source_path=str(path),
            source_kind="archive",
            archive_format="pdf",
            page_count=page_count,
            file_count=1,
            total_bytes=size_bytes,
            metadata=infer_metadata_from_name(path.name),
            pages=(),
            warnings=tuple(warnings),
        )

    if suffix in {".cbr", ".rar"}:
        try:
            pages = _collect_rar_pages(path)
        except IngestError as exc:
            if "no rar/cbr extractor" not in str(exc).lower():
                raise
            warnings.append(f"RAR/CBR page listing unavailable: {exc}")
            pages = ()
        return ScanResult(
            source_path=str(path),
            source_kind="archive",
            archive_format="rar",
            page_count=len(pages),
            file_count=len(pages),
            total_bytes=path.stat().st_size,
            metadata=infer_metadata_from_name(path.name),
            pages=pages,
            warnings=tuple(warnings),
        )

    raise IngestError(f"Unsupported archive format: {path.suffix}")


def scan_source(path: str | Path, recursive: bool = True) -> ScanResult:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise IngestError(f"Source does not exist: {resolved}")
    if resolved.is_dir():
        return scan_directory(resolved, recursive=recursive)
    if resolved.is_file():
        if _is_archive(resolved):
            return scan_archive(resolved)
        if _is_image(resolved):
            page = PageRecord(index=1, relative_path=resolved.name, size_bytes=resolved.stat().st_size, extension=resolved.suffix.lower())
            return ScanResult(
                source_path=str(resolved),
                source_kind="image",
                archive_format=None,
                page_count=1,
                file_count=1,
                total_bytes=resolved.stat().st_size,
                metadata=infer_metadata_from_name(resolved.name),
                pages=(page,),
                warnings=(),
            )
    raise IngestError(f"Unsupported source type: {resolved}")


def discover_sources(path: str | Path, recursive: bool = True) -> list[Path]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise IngestError(f"Source does not exist: {resolved}")

    if resolved.is_file():
        if _is_archive(resolved) or _is_image(resolved):
            return [resolved]
        raise IngestError(f"Unsupported source type: {resolved}")

    if not resolved.is_dir():
        raise IngestError(f"Unsupported source type: {resolved}")

    discovered: list[Path] = []

    def walk(directory: Path) -> None:
        if _has_direct_image_children(directory):
            discovered.append(directory)
            return

        for child in sorted(directory.iterdir(), key=lambda item: _natural_key(item.name)):
            if _is_hidden(child):
                continue
            if child.is_file() and (_is_archive(child) or _is_image(child)):
                discovered.append(child)
            elif child.is_dir() and recursive:
                walk(child)

    walk(resolved)
    return discovered or [resolved]


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, IngestJobRecord] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def create(self, request: IngestJobRequest) -> IngestJobRecord:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._counter += 1
            job_id = f"ingest-{self._counter:06d}"
            record = IngestJobRecord(
                job_id=job_id,
                request=request,
                status=JobStatus.queued,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job_id] = record
            return record

    def list(self) -> list[IngestJobRecord]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)

    def get(self, job_id: str) -> IngestJobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def save(self, record: IngestJobRecord) -> None:
        with self._lock:
            self._jobs[record.job_id] = record


class IngestService:
    def __init__(self, job_store: InMemoryJobStore | None = None) -> None:
        self.job_store = job_store or InMemoryJobStore()

    def preview(self, paths: Sequence[str | Path], recursive: bool = True) -> list[ScanResult]:
        scans: list[ScanResult] = []
        for path in paths:
            for source in discover_sources(path, recursive=recursive):
                scans.append(scan_source(source, recursive=recursive))
        return scans

    def submit(self, paths: Sequence[str | Path], recursive: bool = True) -> IngestJobRecord:
        expanded_paths: list[str] = []
        for path in paths:
            expanded_paths.extend(str(source) for source in discover_sources(path, recursive=recursive))
        request = IngestJobRequest(paths=tuple(expanded_paths), recursive=recursive)
        return self.job_store.create(request)

    def run(self, job_id: str) -> IngestJobRecord:
        record = self.job_store.get(job_id)
        if record is None:
            raise IngestError(f"Unknown ingest job: {job_id}")

        running = IngestJobRecord(
            job_id=record.job_id,
            request=record.request,
            status=JobStatus.running,
            created_at=record.created_at,
            updated_at=datetime.now(timezone.utc),
            scans=record.scans,
            error=None,
        )
        self.job_store.save(running)

        try:
            scans = tuple(scan_source(path, recursive=record.request.recursive) for path in record.request.paths)
            completed = IngestJobRecord(
                job_id=record.job_id,
                request=record.request,
                status=JobStatus.succeeded,
                created_at=record.created_at,
                updated_at=datetime.now(timezone.utc),
                scans=scans,
                error=None,
            )
        except Exception as exc:
            completed = IngestJobRecord(
                job_id=record.job_id,
                request=record.request,
                status=JobStatus.failed,
                created_at=record.created_at,
                updated_at=datetime.now(timezone.utc),
                scans=(),
                error=str(exc),
            )

        self.job_store.save(completed)
        if completed.status is JobStatus.failed:
            raise IngestError(completed.error or "Ingest job failed.")
        return completed

    def get(self, job_id: str) -> IngestJobRecord | None:
        return self.job_store.get(job_id)

    def list_jobs(self) -> list[IngestJobRecord]:
        return self.job_store.list()

    def mark_failed(self, job_id: str, error: str) -> IngestJobRecord:
        existing = self.job_store.get(job_id)
        if existing is None:
            raise IngestError(f"Unknown ingest job: {job_id}")
        failed = IngestJobRecord(
            job_id=existing.job_id,
            request=existing.request,
            status=JobStatus.failed,
            created_at=existing.created_at,
            updated_at=datetime.now(timezone.utc),
            scans=existing.scans,
            error=error,
        )
        self.job_store.save(failed)
        return failed


def ingest_job_payload(job: IngestJobRecord) -> dict:
    return job.to_dict()


def scan_payload(scan: ScanResult) -> dict:
    return scan.to_dict()
