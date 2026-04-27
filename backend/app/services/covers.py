from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import html
import hashlib
import mimetypes
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ReadingPathCoverAsset


SEARCH_URL_TEMPLATE = "https://getcomics.org/?s={query}"
BASE_DIR = Path(__file__).resolve().parent.parent.parent
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


def _guess_extension(source_url: str | None, content_type: str | None) -> str:
    if content_type:
        normalized = content_type.split(";", 1)[0].strip().lower()
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
        asset = ReadingPathCoverAsset(reading_path_id=reading_path_id)
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

    asset.cached_path = str(destination)
    asset.content_type = content_type.split(";", 1)[0].strip().lower() if content_type else None
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

    try:
        content, content_type = _download_binary(cover.image_url)
    except (HTTPError, URLError, TimeoutError, requests.RequestException):
        return None, None, cover

    for sibling in ENTRY_COVER_CACHE_DIR.glob(f"{safe_key}.*"):
        if sibling != destination:
            sibling.unlink(missing_ok=True)

    destination.write_bytes(content)
    normalized_content_type = content_type.split(";", 1)[0].strip().lower() if content_type else None
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
        return existing_path, mimetypes.guess_type(existing_path.name)[0]

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
    normalized_content_type = content_type.split(";", 1)[0].strip().lower() if content_type else None
    return destination, normalized_content_type
