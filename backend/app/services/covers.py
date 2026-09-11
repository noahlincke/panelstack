from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import html
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from .cover_cache import has_room_for_covers, prune_cover_cache
from ..models import ReadingPathCoverAsset


SEARCH_URL_TEMPLATE = "https://getcomics.org/?s={query}"
BASE_DIR = Path(__file__).resolve().parent.parent.parent
COVER_OVERRIDES_PATH = BASE_DIR / "data" / "curation" / "cover_overrides.json"
COVER_CACHE_DIR = BASE_DIR / "data" / "cache" / "reading_path_covers"
ENTRY_COVER_CACHE_DIR = BASE_DIR / "data" / "cache" / "reading_path_entry_covers"
REMOTE_COVER_CACHE_DIR = BASE_DIR / "data" / "cache" / "remote_covers"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)

ARTICLE_PATTERN = re.compile(r"<article\b.*?</article>", flags=re.IGNORECASE | re.DOTALL)
POST_LINK_PATTERN = re.compile(
    r"<h[12][^>]*class=\"[^\"]*post-title[^\"]*\"[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
    flags=re.IGNORECASE | re.DOTALL,
)
IMAGE_PATTERN = re.compile(
    r"<img[^>]+(?:src|data-lazy-src|data-src)=\"([^\"]+)\"",
    flags=re.IGNORECASE | re.DOTALL,
)
OG_IMAGE_PATTERN = re.compile(
    r"<meta[^>]+property=\"og:image\"[^>]+content=\"([^\"]+)\"",
    flags=re.IGNORECASE | re.DOTALL,
)
TAG_PATTERN = re.compile(r"<[^>]+>")
NO_RESULTS_PATTERN = re.compile(r"<body[^>]+search-no-results\b", flags=re.IGNORECASE)


@dataclass(frozen=True)
class GetComicsCoverResult:
    query: str
    image_url: str | None
    post_url: str | None
    post_title: str | None


@dataclass(frozen=True)
class GetComicsSearchCandidate:
    image_url: str | None
    post_url: str | None
    post_title: str | None


def _clean_html_text(value: str) -> str:
    return html.unescape(TAG_PATTERN.sub("", value)).strip()


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = " ".join(normalized.split())
    normalized = re.sub(r"^(the|a|an)\s+", "", normalized)
    return normalized


def _is_rejected_getcomics_image_url(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.lower()
    return (
        "getcomics.info_.png" in normalized
        or "cropped-getcomics-favicon" in normalized
    )


def parse_getcomics_search_candidates(raw_html: str) -> list[GetComicsSearchCandidate]:
    candidates: list[GetComicsSearchCandidate] = []
    for article_html in ARTICLE_PATTERN.findall(raw_html):
        link_match = POST_LINK_PATTERN.search(article_html)
        image_match = IMAGE_PATTERN.search(article_html)
        image_url = html.unescape(image_match.group(1)) if image_match else None
        if _is_rejected_getcomics_image_url(image_url):
            image_url = None
        candidates.append(
            GetComicsSearchCandidate(
                image_url=image_url,
                post_url=html.unescape(link_match.group(1)) if link_match else None,
                post_title=_clean_html_text(link_match.group(2)) if link_match else None,
            )
        )
    return candidates


def parse_getcomics_search_results(raw_html: str, query: str) -> GetComicsCoverResult:
    if NO_RESULTS_PATTERN.search(raw_html):
        return GetComicsCoverResult(query=query, image_url=None, post_url=None, post_title=None)

    candidates = parse_getcomics_search_candidates(raw_html)
    if candidates:
        first = candidates[0]
        return GetComicsCoverResult(
            query=query,
            image_url=first.image_url,
            post_url=first.post_url,
            post_title=first.post_title,
        )

    og_image_match = OG_IMAGE_PATTERN.search(raw_html)
    og_image_url = html.unescape(og_image_match.group(1)) if og_image_match else None
    if _is_rejected_getcomics_image_url(og_image_url):
        og_image_url = None
    return GetComicsCoverResult(
        query=query,
        image_url=og_image_url,
        post_url=None,
        post_title=None,
    )


@lru_cache(maxsize=1)
def cover_overrides() -> dict[str, str]:
    """Hand-picked covers by reading path slug.

    The automatic lookup uses whatever image the best-matching GetComics post
    carries, which is occasionally not a cover: All-Star Superman matched a 2024
    reprint whose post image is the comic's first interior page. An override is
    the honest fix for those, and for books with no usable post at all.
    """
    try:
        payload = json.loads(COVER_OVERRIDES_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return {
        slug: entry["image_url"]
        for slug, entry in payload.get("covers", {}).items()
        if isinstance(entry, dict) and entry.get("image_url")
    }


def override_cover_url(reading_path_slug: str | None) -> str | None:
    return cover_overrides().get(reading_path_slug) if reading_path_slug else None


# Years a GetComics post title carries, e.g. "(2019)" or "(2019-2021)".
TITLE_YEARS_PATTERN = re.compile(r"\((\d{4})(?:\s*[-–]\s*(\d{4}))?\)")


def title_years(post_title: str | None) -> set[int]:
    """Every year a post title claims, expanding a range into its members."""
    if not post_title:
        return set()
    years: set[int] = set()
    for first, last in TITLE_YEARS_PATTERN.findall(post_title):
        start = int(first)
        end = int(last) if last else start
        if end < start or end - start > 60:
            years.update({start, end})
            continue
        years.update(range(start, end + 1))
    return years


def year_conflicts(post_title: str | None, expected_year: int | None) -> bool:
    """Whether a title names years, none of which is the one we want.

    GetComics search is title-only, so "X-Men #1" surfaces a dozen 2026 one-shots
    and never Hickman's 2019 book. Every one of them scored well enough to win,
    and the download handed over "X-Men - Outback #1 (2026)". A title that dates
    itself to another year is simply a different comic.
    """
    if expected_year is None:
        return False
    years = title_years(post_title)
    return bool(years) and expected_year not in years


def _issue_number_tokens(issue_number: str | None) -> set[str]:
    if not issue_number:
        return set()
    normalized = _normalize_text(issue_number)
    return {token for token in normalized.split() if token}


def _is_expected_collected_edition(issue_number: str | None) -> bool:
    return bool(issue_number and "-" in issue_number)


def _score_candidate(
    candidate: GetComicsSearchCandidate,
    *,
    expected_series_title: str | None = None,
    expected_issue_number: str | None = None,
    expected_year: int | None = None,
) -> tuple[int, int, int, int]:
    title = _normalize_text(candidate.post_title)
    expected_series = _normalize_text(expected_series_title)
    expected_issue_tokens = _issue_number_tokens(expected_issue_number)
    expected_collection = _is_expected_collected_edition(expected_issue_number)

    score = 0
    penalties = 0

    if expected_series:
        if title.startswith(expected_series):
            score += 120
        expected_tokens = set(expected_series.split())
        title_tokens = set(title.split())
        score += len(expected_tokens & title_tokens) * 14
        if expected_tokens and not expected_tokens.issubset(title_tokens):
            penalties += len(expected_tokens - title_tokens) * 20

    for token in expected_issue_tokens:
        if token in title.split():
            score += 18

    if expected_year is not None:
        if str(expected_year) in title.split():
            score += 32
        else:
            penalties += 60

    if "infinity comic" in title and "infinity comic" not in expected_series:
        penalties += 90
    if not expected_collection and ("omnibus" in title or "vol " in title or "tpb" in title):
        penalties += 25

    has_image = 1 if candidate.image_url else 0
    exact_title_bonus = 1 if expected_series and title.startswith(expected_series) else 0
    return (score - penalties, exact_title_bonus, has_image, -(len(candidate.post_title or "")))


@lru_cache(maxsize=256)
def fetch_getcomics_cover(
    query: str,
    expected_series_title: str | None = None,
    expected_issue_number: str | None = None,
    expected_year: int | None = None,
) -> GetComicsCoverResult:
    normalized_query = " ".join(query.split()).strip()
    if not normalized_query:
        return GetComicsCoverResult(query=query, image_url=None, post_url=None, post_title=None)

    request = Request(
        SEARCH_URL_TEMPLATE.format(query=quote_plus(normalized_query)),
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw_html = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError):
        return GetComicsCoverResult(query=normalized_query, image_url=None, post_url=None, post_title=None)

    candidates = parse_getcomics_search_candidates(raw_html)
    if candidates:
        best = max(
            candidates,
            key=lambda candidate: _score_candidate(
                candidate,
                expected_series_title=expected_series_title,
                expected_issue_number=expected_issue_number,
                expected_year=expected_year,
            ),
        )
        return GetComicsCoverResult(
            query=normalized_query,
            image_url=best.image_url,
            post_url=best.post_url,
            post_title=best.post_title,
        )

    return parse_getcomics_search_results(raw_html, normalized_query)


@lru_cache(maxsize=256)
def find_getcomics_post(
    query: str,
    expected_series_title: str | None = None,
    expected_issue_number: str | None = None,
    expected_year: int | None = None,
) -> GetComicsCoverResult:
    """Resolve a post to download, refusing a match from the wrong year.

    Cover lookup takes the best-scoring candidate whatever it is, because a
    slightly wrong cover still beats a blank tile. A download has no such
    excuse: handing over the wrong comic wastes the reader's time and bandwidth,
    so a title dated to another year is discarded rather than merely penalised.
    """
    normalized_query = " ".join(query.split()).strip()
    if not normalized_query:
        return GetComicsCoverResult(query=query, image_url=None, post_url=None, post_title=None)

    request = Request(
        SEARCH_URL_TEMPLATE.format(query=quote_plus(normalized_query)),
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw_html = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError):
        return GetComicsCoverResult(query=normalized_query, image_url=None, post_url=None, post_title=None)

    candidates = [
        candidate
        for candidate in parse_getcomics_search_candidates(raw_html)
        if candidate.post_url and not year_conflicts(candidate.post_title, expected_year)
    ]
    if not candidates:
        return GetComicsCoverResult(query=normalized_query, image_url=None, post_url=None, post_title=None)

    best = max(
        candidates,
        key=lambda candidate: _score_candidate(
            candidate,
            expected_series_title=expected_series_title,
            expected_issue_number=expected_issue_number,
            expected_year=expected_year,
        ),
    )
    return GetComicsCoverResult(
        query=normalized_query,
        image_url=best.image_url,
        post_url=best.post_url,
        post_title=best.post_title,
    )


def _guess_extension(source_url: str | None, content_type: str | None) -> str:
    if content_type:
        normalized = content_type.split(";", 1)[0].strip().lower()
        if normalized.startswith("image/"):
            if normalized == "image/jpeg":
                return ".jpg"
            guessed = mimetypes.guess_extension(normalized)
            if guessed:
                return guessed
    if source_url:
        suffix = Path(urlparse(source_url).path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
            return suffix
    return ".img"


def _content_type_for_extension(extension: str) -> str | None:
    if extension == ".webp":
        return "image/webp"
    if extension == ".avif":
        return "image/avif"
    return mimetypes.types_map.get(extension)


def _normalize_image_content_type(source_url: str | None, content_type: str | None) -> str | None:
    normalized = content_type.split(";", 1)[0].strip().lower() if content_type else None
    if normalized and normalized.startswith("image/"):
        return normalized
    extension = _guess_extension(source_url, content_type)
    if extension != ".img":
        return _content_type_for_extension(extension)
    return None


def _download_binary(url: str) -> tuple[bytes, str | None]:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type")


def _download_binary_with_headers(url: str, *, referer_url: str | None = None) -> tuple[bytes, str | None]:
    headers = {"User-Agent": USER_AGENT}
    if referer_url:
        headers["Referer"] = referer_url
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type")


def ensure_reading_path_cover_asset(
    db: Session,
    *,
    reading_path_id: int,
    query: str,
    expected_series_title: str | None = None,
    expected_issue_number: str | None = None,
    expected_year: int | None = None,
    force_refresh: bool = False,
) -> ReadingPathCoverAsset:
    asset = db.scalar(
        select(ReadingPathCoverAsset).where(ReadingPathCoverAsset.reading_path_id == reading_path_id)
    )
    if asset is None:
        # query is NOT NULL, so it has to be set before the row is flushed.
        asset = ReadingPathCoverAsset(reading_path_id=reading_path_id, query=query)
        db.add(asset)
        db.flush()

    asset.query = query

    cached_path = Path(asset.cached_path) if asset.cached_path else None
    cached_ready = cached_path is not None and cached_path.exists()
    if cached_ready and asset.status == "ready" and not force_refresh:
        return asset

    cover = fetch_getcomics_cover(
        query,
        expected_series_title=expected_series_title,
        expected_issue_number=expected_issue_number,
        expected_year=expected_year,
    )
    asset.post_url = cover.post_url
    asset.post_title = cover.post_title
    asset.source_image_url = cover.image_url

    if not cover.image_url:
        asset.status = "missing"
        asset.error = "No cover image could be resolved."
        db.flush()
        return asset

    if cached_ready and asset.source_image_url == cover.image_url and not force_refresh:
        asset.status = "ready"
        asset.error = None
        db.flush()
        return asset

    # Never spend the last of the host's disk on a cover.
    if not has_room_for_covers():
        asset.status = "failed"
        asset.error = "Not enough free disk space to cache a cover."
        db.flush()
        return asset

    COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        content, content_type = _download_binary(cover.image_url)
    except (HTTPError, URLError, TimeoutError, requests.RequestException) as exc:
        asset.status = "error"
        asset.error = str(exc)
        db.flush()
        return asset

    extension = _guess_extension(cover.image_url, content_type)
    digest = hashlib.sha1(cover.image_url.encode("utf-8")).hexdigest()[:12]
    destination = COVER_CACHE_DIR / f"reading-path-{reading_path_id}-{digest}{extension}"
    destination.write_bytes(content)
    prune_cover_cache()

    asset.cached_path = str(destination)
    asset.content_type = _normalize_image_content_type(cover.image_url, content_type)
    asset.status = "ready"
    asset.error = None
    db.flush()
    return asset


def _safe_cache_key(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
    return normalized or "cover"


def ensure_query_cover_image(
    *,
    cache_key: str,
    query: str,
    expected_series_title: str | None = None,
    expected_issue_number: str | None = None,
    expected_year: int | None = None,
    force_refresh: bool = False,
) -> tuple[Path | None, str | None, GetComicsCoverResult]:
    cover = fetch_getcomics_cover(
        query,
        expected_series_title=expected_series_title,
        expected_issue_number=expected_issue_number,
        expected_year=expected_year,
    )
    if not cover.image_url:
        return None, None, cover

    safe_key = _safe_cache_key(cache_key)
    extension = Path(urlparse(cover.image_url).path).suffix.lower() or ".img"
    if extension not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        extension = ".img"

    ENTRY_COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    destination = ENTRY_COVER_CACHE_DIR / f"{safe_key}{extension}"
    if destination.exists() and not force_refresh:
        content_type = mimetypes.guess_type(destination.name)[0]
        return destination, content_type, cover

    # Never spend the last of the host's disk on a cover.
    if not has_room_for_covers():
        return None, None, cover

    try:
        content, content_type = _download_binary(cover.image_url)
    except (HTTPError, URLError, TimeoutError, requests.RequestException):
        return None, None, cover

    for sibling in ENTRY_COVER_CACHE_DIR.glob(f"{safe_key}.*"):
        if sibling != destination:
            sibling.unlink(missing_ok=True)

    destination.write_bytes(content)
    prune_cover_cache()
    normalized_content_type = _normalize_image_content_type(cover.image_url, content_type)
    return destination, normalized_content_type, cover


def ensure_remote_cover_image(
    *,
    cache_key: str,
    image_url: str,
    referer_url: str | None = None,
    force_refresh: bool = False,
) -> tuple[Path | None, str | None]:
    safe_key = _safe_cache_key(cache_key)
    digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:12]
    REMOTE_COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    existing_matches = sorted(REMOTE_COVER_CACHE_DIR.glob(f"{safe_key}.*"))
    extension_from_url = Path(urlparse(image_url).path).suffix.lower()
    if extension_from_url not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        extension_from_url = ".img"
    destination = REMOTE_COVER_CACHE_DIR / f"{safe_key}-{digest}{extension_from_url}"

    if destination.exists() and not force_refresh:
        existing_path = destination
        return existing_path, _content_type_for_extension(existing_path.suffix.lower())

    # Never spend the last of the host's disk on a cover.
    if not has_room_for_covers():
        return None, None

    try:
        content, content_type = _download_binary_with_headers(image_url, referer_url=referer_url)
    except (HTTPError, URLError, TimeoutError, requests.RequestException):
        return None, None

    extension = _guess_extension(image_url, content_type)
    destination = REMOTE_COVER_CACHE_DIR / f"{safe_key}-{digest}{extension}"
    for sibling in existing_matches:
        if sibling != destination:
            sibling.unlink(missing_ok=True)

    destination.write_bytes(content)
    prune_cover_cache()
    normalized_content_type = _normalize_image_content_type(image_url, content_type)
    return destination, normalized_content_type
