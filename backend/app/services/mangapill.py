from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from html import unescape
import json
import re
from pathlib import Path
from typing import TypeVar
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..models import CanonicalIssue, CanonicalSeries, Publisher, ReadingPath, ReadingPathEntry
from .library import issue_sort_order, slugify


USER_AGENT = "Mozilla/5.0"
PROVIDER_NAME = "MangaPill"
MANGAPILL_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "providers" / "mangapill.json"
CHAPTER_LINK_PATTERN = re.compile(r"^/chapters/(?P<series>\d+)-(?P<chapter_code>\d+)/")
IMAGE_URL_PATTERN = re.compile(r"https://cdn\.readdetectiveconan\.com/file/mangap/[^\"']+\.(?:jpg|jpeg|png|webp)", re.I)
T = TypeVar("T")


@dataclass(frozen=True)
class MangaPillChapter:
    provider_issue_id: str
    issue_number: str
    title: str
    chapter_url: str
    page_count: int | None = None


@dataclass(frozen=True)
class MangaPillSeriesPayload:
    title: str
    description: str
    cover_url: str | None
    chapters: list[MangaPillChapter]


@dataclass(frozen=True)
class MangaPillCollectionGroup:
    slug_suffix: str
    title: str
    description: str | None
    issue_numbers: list[str]
    cover_url: str | None = None


def _fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def _clean_description(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", unescape(raw))
    return re.sub(r"\s+", " ", text).strip()


def _issue_number_from_chapter_slug(chapter_slug: str) -> str:
    match = re.search(r"chapter-([0-9]+(?:\.[0-9]+)?)", chapter_slug, flags=re.I)
    if match:
        return match.group(1)
    return chapter_slug.rsplit("-", 1)[-1]


def fetch_mangapill_series(title_url: str) -> MangaPillSeriesPayload:
    html = _fetch_html(title_url)
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    title_meta = soup.find("meta", attrs={"property": "og:title"})
    if title_meta and title_meta.get("content"):
        title = str(title_meta["content"]).replace(" Manga - Mangapill", "").strip()
    if not title:
        title = "Manga Series"

    description_meta = soup.find("meta", attrs={"name": "description"})
    description = _clean_description(description_meta.get("content") if description_meta else None)

    cover_meta = soup.find("meta", attrs={"property": "og:image"})
    cover_url = str(cover_meta["content"]).strip() if cover_meta and cover_meta.get("content") else None

    chapters_by_issue: dict[str, MangaPillChapter] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not CHAPTER_LINK_PATTERN.match(href):
            continue
        chapter_url = urljoin(title_url, href)
        issue_number = _issue_number_from_chapter_slug(href)
        provider_issue_id = href.strip("/").split("/")[1]
        chapter_title = f"{title} Chapter {issue_number}"
        chapters_by_issue[issue_number] = MangaPillChapter(
            provider_issue_id=provider_issue_id,
            issue_number=issue_number,
            title=chapter_title,
            chapter_url=chapter_url,
        )

    ordered = sorted(
        chapters_by_issue.values(),
        key=lambda item: (issue_sort_order(item.issue_number), item.issue_number),
    )
    return MangaPillSeriesPayload(
        title=title,
        description=description,
        cover_url=cover_url,
        chapters=ordered,
    )


def fetch_mangapill_chapter_pages(chapter_url: str) -> list[str]:
    html = _fetch_html(chapter_url)
    return list(dict.fromkeys(IMAGE_URL_PATTERN.findall(html)))


def _upsert_publisher(db: Session, *, slug: str, name: str, description: str | None = None) -> Publisher:
    publisher = db.scalar(select(Publisher).where(Publisher.slug == slug))
    if publisher is None:
        publisher = Publisher(slug=slug, name=name)
        db.add(publisher)
    publisher.name = name
    publisher.description = description
    db.flush()
    return publisher


def _chunked(values: list[T], size: int) -> list[list[T]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


@lru_cache(maxsize=1)
def _mangapill_config_payload() -> dict:
    if not MANGAPILL_DATA_PATH.exists():
        return {"series": []}
    return json.loads(MANGAPILL_DATA_PATH.read_text())


def _normalize_issue_token(value: str | int | float) -> str:
    text = str(value).strip()
    if not text:
        return text
    if re.fullmatch(r"\d+", text):
        return str(int(text))
    return text


def _expand_issue_numbers(start_issue: str | int | float, end_issue: str | int | float) -> list[str]:
    start_text = _normalize_issue_token(start_issue)
    end_text = _normalize_issue_token(end_issue)
    if "." in start_text or "." in end_text:
        if start_text == end_text:
            return [start_text]
        raise ValueError(f"Decimal issue ranges must be explicit: {start_text}-{end_text}")
    start_number = int(start_text)
    end_number = int(end_text)
    if end_number < start_number:
        raise ValueError(f"Invalid issue range: {start_text}-{end_text}")
    return [str(number) for number in range(start_number, end_number + 1)]


def _collection_groups(entry: dict, series_payload: MangaPillSeriesPayload) -> list[MangaPillCollectionGroup]:
    configured_groups = entry.get("groups")
    if configured_groups:
        groups: list[MangaPillCollectionGroup] = []
        for group in configured_groups:
            issue_numbers: list[str]
            if group.get("all_issues"):
                issue_numbers = [chapter.issue_number for chapter in series_payload.chapters]
            elif group.get("issue_numbers"):
                issue_numbers = [_normalize_issue_token(value) for value in group["issue_numbers"]]
            else:
                issue_numbers = _expand_issue_numbers(group["start_issue"], group["end_issue"])
            groups.append(
                MangaPillCollectionGroup(
                    slug_suffix=group["slug_suffix"],
                    title=group["title"],
                    description=group.get("description"),
                    issue_numbers=issue_numbers,
                    cover_url=group.get("cover_url"),
                )
            )
        return groups

    chunk_size = int(entry.get("chunk_size", 25))
    groups = []
    for chapter_chunk in _chunked(series_payload.chapters, chunk_size):
        start_issue = chapter_chunk[0].issue_number
        end_issue = chapter_chunk[-1].issue_number
        groups.append(
            MangaPillCollectionGroup(
                slug_suffix=f"ch-{slugify(start_issue)}-{slugify(end_issue)}",
                title=f"{series_payload.title}: Ch. {start_issue}-{end_issue}",
                description=f"English chapters {start_issue} to {end_issue} streamed from {PROVIDER_NAME}.",
                issue_numbers=[chapter.issue_number for chapter in chapter_chunk],
                cover_url=None,
            )
        )
    return groups


def get_mangapill_collection_cover_url(reading_path_slug: str) -> str | None:
    payload = _mangapill_config_payload()
    for series in payload.get("series", []):
        base_slug = series.get("slug")
        for group in series.get("groups", []):
            slug_suffix = group.get("slug_suffix")
            if not base_slug or not slug_suffix:
                continue
            if f"{base_slug}-en-{slug_suffix}" == reading_path_slug:
                return group.get("cover_url")
    return None


def sync_mangapill_catalog(db: Session, config_path: Path | None = None) -> int:
    path = config_path or MANGAPILL_DATA_PATH
    if not path.exists():
        return 0

    payload = json.loads(path.read_text())
    synced_paths = 0

    for entry in payload.get("series", []):
        title_url = entry["title_url"]
        series_payload = fetch_mangapill_series(title_url)
        publisher = _upsert_publisher(
            db,
            slug=entry.get("publisher_slug", "manga"),
            name=entry.get("publisher_name", "Manga"),
            description="Externally streamed manga catalog metadata.",
        )

        series_slug = entry["slug"]
        canonical_series = db.scalar(select(CanonicalSeries).where(CanonicalSeries.slug == series_slug))
        if canonical_series is None:
            canonical_series = CanonicalSeries(slug=series_slug, title=entry.get("title", series_payload.title))
            db.add(canonical_series)

        canonical_series.publisher_id = publisher.id
        canonical_series.title = entry.get("title", series_payload.title)
        canonical_series.description = series_payload.description or entry.get("description")
        canonical_series.start_year = entry.get("start_year")
        canonical_series.end_year = entry.get("end_year")
        canonical_series.provider_name = PROVIDER_NAME
        canonical_series.provider_series_id = entry.get("provider_series_id")
        canonical_series.provider_url = title_url
        canonical_series.cover_url = series_payload.cover_url
        db.flush()

        issues_by_key = {
            issue.issue_number: issue
            for issue in db.scalars(select(CanonicalIssue).where(CanonicalIssue.series_id == canonical_series.id)).all()
        }
        for chapter in series_payload.chapters:
            legacy_key = f"{canonical_series.slug}#{chapter.issue_number}"
            canonical_issue = db.scalar(select(CanonicalIssue).where(CanonicalIssue.legacy_key == legacy_key))
            if canonical_issue is None:
                canonical_issue = issues_by_key.get(chapter.issue_number)
            if canonical_issue is None:
                canonical_issue = CanonicalIssue(series_id=canonical_series.id, legacy_key=legacy_key, issue_number=chapter.issue_number)
                db.add(canonical_issue)

            canonical_issue.series_id = canonical_series.id
            canonical_issue.legacy_key = legacy_key
            canonical_issue.issue_number = chapter.issue_number
            canonical_issue.issue_kind = "issue"
            canonical_issue.title = chapter.title
            canonical_issue.sort_order = issue_sort_order(chapter.issue_number)
            canonical_issue.published_on = None
            canonical_issue.summary = None
            canonical_issue.provider_name = PROVIDER_NAME
            canonical_issue.provider_issue_id = chapter.provider_issue_id
            canonical_issue.provider_url = chapter.chapter_url
            canonical_issue.cover_url = series_payload.cover_url
            canonical_issue.page_count = chapter.page_count
            db.flush()

        chapters_by_issue = {chapter.issue_number: chapter for chapter in series_payload.chapters}
        groups = _collection_groups(entry, series_payload)
        desired_slugs = {f"{canonical_series.slug}-en-{group.slug_suffix}" for group in groups}
        provider_series_id = str(entry.get("provider_series_id") or "").strip()
        source_match_clauses = [ReadingPath.source_url == title_url]
        if provider_series_id:
            source_match_clauses.append(ReadingPath.source_url.like(f"https://mangapill.com/manga/{provider_series_id}%"))
        obsolete_paths = db.scalars(
            select(ReadingPath).where(
                ReadingPath.source_name == PROVIDER_NAME,
                or_(*source_match_clauses),
                ReadingPath.slug.not_in(desired_slugs),
            )
        ).all()
        for obsolete in obsolete_paths:
            db.delete(obsolete)
        db.flush()

        for group in groups:
            chapter_chunk = [chapters_by_issue[issue_number] for issue_number in group.issue_numbers if issue_number in chapters_by_issue]
            if not chapter_chunk:
                continue
            path_slug = f"{canonical_series.slug}-en-{group.slug_suffix}"
            reading_path = db.scalar(select(ReadingPath).where(ReadingPath.slug == path_slug))
            if reading_path is None:
                reading_path = ReadingPath(slug=path_slug, title=group.title)
                db.add(reading_path)

            reading_path.event_id = None
            reading_path.title = group.title
            reading_path.description = group.description or f"English chapters streamed from {PROVIDER_NAME}."
            reading_path.status = "published"
            reading_path.source_name = PROVIDER_NAME
            reading_path.source_url = title_url
            db.flush()

            db.execute(delete(ReadingPathEntry).where(ReadingPathEntry.reading_path_id == reading_path.id))
            db.flush()

            for index, chapter in enumerate(chapter_chunk, start=1):
                canonical_issue = db.scalar(
                    select(CanonicalIssue).where(
                        CanonicalIssue.series_id == canonical_series.id,
                        CanonicalIssue.issue_number == chapter.issue_number,
                    )
                )
                if canonical_issue is None:
                    continue
                db.add(
                    ReadingPathEntry(
                        reading_path_id=reading_path.id,
                        canonical_series_id=canonical_series.id,
                        canonical_issue_id=canonical_issue.id,
                        story_arc_id=None,
                        series_id=None,
                        issue_id=None,
                        sort_order=index * 10,
                        entry_type="issue",
                        importance="main",
                        label=None,
                        note=None,
                        is_optional=False,
                    )
                )
            synced_paths += 1

    db.commit()
    return synced_paths
