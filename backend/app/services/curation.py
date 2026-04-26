from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    CanonicalIssue,
    CanonicalSeries,
    Event,
    Issue,
    IssueMatch,
    Publisher,
    ReadingPath,
    ReadingPathEntry,
    Series,
    StoryArc,
)
from .library import issue_sort_order, slugify


BASE_DIR = Path(__file__).resolve().parent.parent.parent
CURATION_DATA_PATH = BASE_DIR / "data" / "curation" / "reading_paths.json"
SERIES_STOPWORDS = {"the", "a", "an", "and", "comic", "comics"}


@dataclass(frozen=True)
class CurationSyncResult:
    publishers_synced: int = 0
    events_synced: int = 0
    story_arcs_synced: int = 0
    canonical_series_synced: int = 0
    canonical_issues_synced: int = 0
    reading_paths_synced: int = 0
    reading_path_entries_synced: int = 0
    issue_matches_synced: int = 0


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _normalize_issue_number(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.strip().lstrip("#")
    normalized = normalized.lower()
    normalized = re.sub(r"\d+", lambda match: str(int(match.group(0))), normalized)
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _canonical_issue_key(series_slug: str, issue_number: str) -> str:
    return f"{series_slug}#{_normalize_issue_number(issue_number)}"


def _normalize_issue_kind(value: str | None) -> str:
    if not value:
        return "issue"
    normalized = value.strip().lower()
    if normalized in {"trade", "tpb", "hardcover", "omnibus"}:
        return "collection"
    return normalized


def _compatible_issue_kinds(local_kind: str) -> set[str]:
    normalized = _normalize_issue_kind(local_kind)
    if normalized == "issue":
        return {"issue", "one-shot", "special"}
    if normalized == "annual":
        return {"annual"}
    if normalized in {"one-shot", "special"}:
        return {"issue", "one-shot", "special"}
    if normalized == "collection":
        return {"collection"}
    return {normalized}


def load_curation_payload(data_path: Path | None = None) -> dict:
    path = data_path or CURATION_DATA_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_series_name(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower().replace("&", " and ")
    normalized = re.sub(r"\b(?:vol(?:ume)?|v)\.?\s*\d+\b", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    tokens = [token for token in normalized.split() if token and token not in SERIES_STOPWORDS]
    return " ".join(tokens)


def _series_name_variants(title: str, aliases: list[str] | None = None) -> set[str]:
    variants = {_normalize_series_name(title)}
    if aliases:
        variants.update(_normalize_series_name(alias) for alias in aliases)
    raw_variants = {title, *(aliases or [])}
    for variant in raw_variants:
        normalized = variant.lower().replace("&", " and ")
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        variants.add(" ".join(normalized.split()))
    return {variant for variant in variants if variant}


def _token_overlap_score(left: str, right: str) -> int:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
    return int(overlap * 30)


def _series_similarity_score(local_title: str, canonical_variants: set[str]) -> tuple[int, str]:
    local_normalized = _normalize_series_name(local_title)
    local_basic = " ".join(re.sub(r"[^a-z0-9]+", " ", local_title.lower().replace("&", " and ")).split())
    best_score = 0
    best_reason = "no-title-match"

    for canonical_variant in canonical_variants:
        if not canonical_variant:
            continue
        if local_normalized == canonical_variant or local_basic == canonical_variant:
            return 70, "exact-series-title"

        score = 0
        reason = "fuzzy-series-title"
        if local_normalized and canonical_variant and (
            local_normalized in canonical_variant or canonical_variant in local_normalized
        ):
            score += 42
            reason = "contained-series-title"

        score += _token_overlap_score(local_normalized, canonical_variant)
        score += int(difflib.SequenceMatcher(a=local_normalized, b=canonical_variant).ratio() * 28)

        if score > best_score:
            best_score = score
            best_reason = reason

    return best_score, best_reason


def _series_fallback_score(
    local_series: Series,
    canonical_series: CanonicalSeries,
    aliases: list[str] | None = None,
    inferred_volume: int | None = None,
) -> tuple[int, str]:
    score, reason = _series_similarity_score(local_series.title, _series_name_variants(canonical_series.title, aliases))
    if local_series.publisher and canonical_series.publisher:
        if slugify(local_series.publisher) == slugify(canonical_series.publisher.name):
            score += 10
            reason = f"{reason}+publisher"
    if local_series.start_year and canonical_series.start_year:
        if local_series.start_year == canonical_series.start_year:
            score += 14
            reason = f"{reason}+year"
        elif abs(local_series.start_year - canonical_series.start_year) <= 1:
            score += 8
            reason = f"{reason}+near-year"
    if inferred_volume and canonical_series.volume:
        if inferred_volume == canonical_series.volume:
            score += 16
            reason = "series-volume+title"
        else:
            score -= 8
    return score, reason


def _is_import_stub_description(value: str | None) -> bool:
    if not value:
        return True
    return value.startswith("Imported from local library:")


def _upsert_publisher(db: Session, payload: dict) -> Publisher:
    publisher = db.scalar(select(Publisher).where(Publisher.slug == payload["slug"]))
    if publisher is None:
        publisher = Publisher(slug=payload["slug"], name=payload["name"])
        db.add(publisher)

    publisher.name = payload["name"]
    publisher.description = payload.get("description")
    db.flush()
    return publisher


def _upsert_event(db: Session, payload: dict, publishers: dict[str, Publisher]) -> Event:
    event = db.scalar(select(Event).where(Event.slug == payload["slug"]))
    if event is None:
        event = Event(slug=payload["slug"], title=payload["title"])
        db.add(event)

    publisher = publishers.get(payload.get("publisher_slug", ""))
    event.publisher_id = publisher.id if publisher else None
    event.title = payload["title"]
    event.description = payload.get("description")
    event.status = payload.get("status", "active")
    event.start_year = payload.get("start_year")
    event.end_year = payload.get("end_year")
    event.source_name = payload.get("source_name")
    event.source_url = payload.get("source_url")
    db.flush()
    return event


def _upsert_story_arc(db: Session, payload: dict, events: dict[str, Event]) -> StoryArc:
    story_arc = db.scalar(select(StoryArc).where(StoryArc.slug == payload["slug"]))
    if story_arc is None:
        story_arc = StoryArc(slug=payload["slug"], title=payload["title"])
        db.add(story_arc)

    event = events.get(payload.get("event_slug", ""))
    story_arc.event_id = event.id if event else None
    story_arc.title = payload["title"]
    story_arc.description = payload.get("description")
    story_arc.phase = payload.get("phase")
    story_arc.status = payload.get("status", "active")
    db.flush()
    return story_arc


def _upsert_canonical_series(
    db: Session,
    payload: dict,
    publishers: dict[str, Publisher],
) -> CanonicalSeries:
    series = db.scalar(select(CanonicalSeries).where(CanonicalSeries.slug == payload["slug"]))
    if series is None:
        series = CanonicalSeries(slug=payload["slug"], title=payload["title"])
        db.add(series)

    publisher = publishers.get(payload.get("publisher_slug", ""))
    series.publisher_id = publisher.id if publisher else None
    series.title = payload["title"]
    series.volume = payload.get("volume")
    series.description = payload.get("description")
    series.start_year = payload.get("start_year")
    series.end_year = payload.get("end_year")
    db.flush()
    return series


def _upsert_canonical_issue(
    db: Session,
    series: CanonicalSeries,
    payload: dict,
    events: dict[str, Event],
) -> CanonicalIssue:
    legacy_key = payload.get("legacy_key") or _canonical_issue_key(series.slug, payload["issue_number"])
    issue = db.scalar(select(CanonicalIssue).where(CanonicalIssue.legacy_key == legacy_key))
    if issue is None:
        issue = CanonicalIssue(
            series_id=series.id,
            legacy_key=legacy_key,
            issue_number=payload["issue_number"],
        )
        db.add(issue)

    event = events.get(payload.get("event_slug", ""))
    issue.series_id = series.id
    issue.event_id = event.id if event else None
    issue.legacy_key = legacy_key
    issue.issue_number = payload["issue_number"]
    issue.issue_kind = _normalize_issue_kind(payload.get("issue_kind"))
    issue.title = payload.get("title")
    issue.sort_order = payload.get("sort_order", issue_sort_order(payload["issue_number"]))
    issue.published_on = _parse_date(payload.get("published_on"))
    issue.summary = payload.get("summary")
    db.flush()
    return issue


def _upsert_reading_path(
    db: Session,
    payload: dict,
    events: dict[str, Event],
) -> ReadingPath:
    reading_path = db.scalar(select(ReadingPath).where(ReadingPath.slug == payload["slug"]))
    if reading_path is None:
        reading_path = ReadingPath(slug=payload["slug"], title=payload["title"])
        db.add(reading_path)

    event = events.get(payload.get("event_slug", ""))
    reading_path.event_id = event.id if event else None
    reading_path.title = payload["title"]
    reading_path.description = payload.get("description")
    reading_path.status = payload.get("status", "published")
    reading_path.source_name = payload.get("source_name")
    reading_path.source_url = payload.get("source_url")
    db.flush()
    return reading_path


def sync_series_matches(db: Session, series_ids: list[int] | None = None) -> int:
    payload = load_curation_payload()
    aliases_by_slug = {
        entry["slug"]: list(entry.get("aliases", []))
        for entry in payload.get("series", [])
        if entry.get("slug")
    }

    canonical_series_list = db.scalars(
        select(CanonicalSeries).options(selectinload(CanonicalSeries.publisher), selectinload(CanonicalSeries.issues))
    ).all()
    if not canonical_series_list:
        return 0

    stmt = select(Series).options(
        selectinload(Series.issues).selectinload(Issue.canonical_matches).selectinload(IssueMatch.canonical_issue),
        selectinload(Series.canonical_series),
    )
    if series_ids:
        stmt = stmt.where(Series.id.in_(series_ids))
    local_series_list = db.scalars(stmt).all()

    synced = 0
    for local_series in local_series_list:
        candidate_series: CanonicalSeries | None = None
        confidence = 0
        strategy = None

        issue_match_candidates: dict[int, list[int]] = {}
        primary_matches = db.scalars(
            select(IssueMatch)
            .join(Issue, IssueMatch.local_issue_id == Issue.id)
            .options(selectinload(IssueMatch.canonical_issue))
            .where(Issue.series_id == local_series.id, IssueMatch.is_primary.is_(True))
        ).all()
        for match in primary_matches:
            if match.canonical_issue is None:
                continue
            issue_match_candidates.setdefault(match.canonical_issue.series_id, []).append(match.confidence_score)

        if issue_match_candidates:
            best_series_id, scores = max(
                issue_match_candidates.items(),
                key=lambda item: (len(item[1]), sum(item[1]), -item[0]),
            )
            candidate_series = next((item for item in canonical_series_list if item.id == best_series_id), None)
            confidence = min(99, int(sum(scores) / len(scores)) + min(12, len(scores) * 4))
            strategy = "issue-match-majority"
        else:
            inferred_volume = next((issue.volume for issue in local_series.issues if issue.volume), None)
            ranked = [
                (
                    canonical,
                    *_series_fallback_score(
                        local_series,
                        canonical,
                        aliases_by_slug.get(canonical.slug, []),
                        inferred_volume=inferred_volume,
                    ),
                )
                for canonical in canonical_series_list
            ]
            if ranked:
                candidate_series, confidence, strategy = max(ranked, key=lambda item: item[1])
                if confidence < 52:
                    candidate_series = None

        if candidate_series is None or strategy is None:
            continue

        updated = False
        if local_series.canonical_series_id != candidate_series.id:
            local_series.canonical_series_id = candidate_series.id
            updated = True
        if local_series.canonical_match_strategy != strategy:
            local_series.canonical_match_strategy = strategy
            updated = True
        if local_series.canonical_match_confidence != confidence:
            local_series.canonical_match_confidence = confidence
            updated = True

        canonical_publisher_name = candidate_series.publisher.name if candidate_series.publisher else None
        if canonical_publisher_name and local_series.publisher != canonical_publisher_name:
            local_series.publisher = canonical_publisher_name
            updated = True
        if candidate_series.start_year and local_series.start_year != candidate_series.start_year:
            local_series.start_year = candidate_series.start_year
            updated = True
        if candidate_series.end_year and local_series.end_year != candidate_series.end_year:
            local_series.end_year = candidate_series.end_year
            updated = True
        if candidate_series.description and _is_import_stub_description(local_series.description):
            if local_series.description != candidate_series.description:
                local_series.description = candidate_series.description
                updated = True

        if updated:
            synced += 1

    db.flush()
    return synced


def sync_issue_matches(db: Session, issue_ids: list[int] | None = None) -> int:
    payload = load_curation_payload()
    aliases_by_slug = {
        entry["slug"]: list(entry.get("aliases", []))
        for entry in payload.get("series", [])
        if entry.get("slug")
    }

    canonical_issues = db.scalars(
        select(CanonicalIssue).options(
            selectinload(CanonicalIssue.series).selectinload(CanonicalSeries.publisher)
        )
    ).all()
    if not canonical_issues:
        return 0

    canonical_lookup: dict[str, list[CanonicalIssue]] = {}
    canonical_special_lookup: dict[str, list[CanonicalIssue]] = {}
    for issue in canonical_issues:
        key = _normalize_issue_number(issue.issue_number)
        canonical_lookup.setdefault(key, []).append(issue)
        if issue.title:
            canonical_special_lookup.setdefault(_normalize_series_name(issue.title), []).append(issue)

    stmt = select(Issue).options(
        selectinload(Issue.series),
        selectinload(Issue.canonical_matches),
    )
    if issue_ids:
        stmt = stmt.where(Issue.id.in_(issue_ids))
    local_issues = db.scalars(stmt).all()

    synced = 0
    for local_issue in local_issues:
        compatible_kinds = _compatible_issue_kinds(local_issue.issue_kind)
        normalized_number = _normalize_issue_number(local_issue.issue_number)
        candidates = [
            candidate
            for candidate in canonical_lookup.get(normalized_number, [])
            if _normalize_issue_kind(candidate.issue_kind) in compatible_kinds
        ]
        if local_issue.series.canonical_series_id:
            canonical_series_candidates = [
                candidate for candidate in candidates if candidate.series_id == local_issue.series.canonical_series_id
            ]
            if canonical_series_candidates:
                candidates = canonical_series_candidates
        if not candidates and local_issue.issue_kind in {"one-shot", "special", "collection"}:
            candidates = [
                candidate
                for candidate in canonical_special_lookup.get(_normalize_series_name(local_issue.title or local_issue.series.title), [])
                if _normalize_issue_kind(candidate.issue_kind) in compatible_kinds
            ]
        if not candidates:
            continue

        def candidate_score(candidate: CanonicalIssue) -> tuple[int, str]:
            aliases = aliases_by_slug.get(candidate.series.slug, [])
            series_score, reason = _series_similarity_score(local_issue.series.title, _series_name_variants(candidate.series.title, aliases))
            score = series_score

            if _normalize_issue_kind(local_issue.issue_kind) == _normalize_issue_kind(candidate.issue_kind):
                score += 12
                reason = f"{reason}+kind"
            elif _normalize_issue_kind(candidate.issue_kind) in {"one-shot", "special"} and _normalize_issue_kind(local_issue.issue_kind) == "issue":
                score += 4

            if local_issue.series.publisher and candidate.series.publisher:
                if slugify(local_issue.series.publisher) == slugify(candidate.series.publisher.name):
                    score += 8

            if local_issue.volume and candidate.series.volume:
                if local_issue.volume == candidate.series.volume:
                    score += 12
                    reason = "volume-aware-match"
                else:
                    score -= 6

            if local_issue.title and candidate.title and local_issue.title == candidate.title:
                score += 5
            elif local_issue.title and candidate.title:
                score += int(difflib.SequenceMatcher(
                    a=_normalize_series_name(local_issue.title),
                    b=_normalize_series_name(candidate.title),
                ).ratio() * 18)
            if local_issue.series.start_year and candidate.published_on:
                if local_issue.series.start_year == candidate.published_on.year:
                    score += 6
            elif local_issue.series.start_year and candidate.series.start_year:
                if abs(local_issue.series.start_year - candidate.series.start_year) <= 1:
                    score += 3
            return score, reason

        ranked_candidates = [
            (candidate, *candidate_score(candidate))
            for candidate in candidates
        ]
        canonical_issue, confidence, match_reason = max(ranked_candidates, key=lambda item: item[1])
        if confidence < 45:
            continue

        existing = next(
            (match for match in local_issue.canonical_matches if match.canonical_issue_id == canonical_issue.id),
            None,
        )
        strategy = "series-title+issue-number" if match_reason.startswith("exact-series-title") else "fuzzy-series+issue-number"
        if existing is None:
            for match in local_issue.canonical_matches:
                match.is_primary = False
            db.add(
                IssueMatch(
                    local_issue_id=local_issue.id,
                    canonical_issue_id=canonical_issue.id,
                    match_strategy=strategy,
                    confidence_score=confidence,
                    is_primary=True,
                    note=(
                        f"Matched {local_issue.series.title} #{local_issue.issue_number} to "
                        f"{canonical_issue.legacy_key} using {match_reason}."
                    ),
                )
            )
            synced += 1
            continue

        updated = False
        if not existing.is_primary:
            for match in local_issue.canonical_matches:
                match.is_primary = False
            existing.is_primary = True
            updated = True
        if existing.confidence_score != confidence:
            existing.confidence_score = confidence
            updated = True
        if existing.match_strategy != strategy:
            existing.match_strategy = strategy
            updated = True
        new_note = (
            f"Matched {local_issue.series.title} #{local_issue.issue_number} to "
            f"{canonical_issue.legacy_key} using {match_reason}."
        )
        if existing.note != new_note:
            existing.note = new_note
            updated = True
        if updated:
            synced += 1

    db.flush()
    return synced


def sync_curation_data(db: Session, data_path: Path | None = None) -> CurationSyncResult:
    payload = load_curation_payload(data_path)
    if not payload:
        return CurationSyncResult()

    publishers: dict[str, Publisher] = {}
    for publisher_payload in payload.get("publishers", []):
        publisher = _upsert_publisher(db, publisher_payload)
        publishers[publisher.slug] = publisher

    events: dict[str, Event] = {}
    for event_payload in payload.get("events", []):
        event = _upsert_event(db, event_payload, publishers)
        events[event.slug] = event

    story_arcs: dict[str, StoryArc] = {}
    for story_arc_payload in payload.get("story_arcs", []):
        story_arc = _upsert_story_arc(db, story_arc_payload, events)
        story_arcs[story_arc.slug] = story_arc

    canonical_series: dict[str, CanonicalSeries] = {}
    canonical_issues: dict[str, CanonicalIssue] = {}
    canonical_issue_count = 0
    for series_payload in payload.get("series", []):
        series = _upsert_canonical_series(db, series_payload, publishers)
        canonical_series[series.slug] = series
        for issue_payload in series_payload.get("issues", []):
            issue = _upsert_canonical_issue(db, series, issue_payload, events)
            canonical_issues[issue.legacy_key] = issue
            canonical_issue_count += 1

    reading_path_entries_count = 0
    for reading_path_payload in payload.get("reading_paths", []):
        reading_path = _upsert_reading_path(db, reading_path_payload, events)
        db.execute(delete(ReadingPathEntry).where(ReadingPathEntry.reading_path_id == reading_path.id))
        db.flush()

        for entry_payload in reading_path_payload.get("entries", []):
            canonical_issue = canonical_issues.get(entry_payload.get("canonical_issue_key", ""))
            canonical_series_ref = canonical_series.get(entry_payload.get("canonical_series_slug", ""))
            story_arc = story_arcs.get(entry_payload.get("story_arc_slug", ""))

            entry = ReadingPathEntry(
                reading_path_id=reading_path.id,
                canonical_series_id=canonical_series_ref.id if canonical_series_ref else None,
                canonical_issue_id=canonical_issue.id if canonical_issue else None,
                story_arc_id=story_arc.id if story_arc else None,
                series_id=None,
                issue_id=None,
                sort_order=entry_payload["sort_order"],
                entry_type=entry_payload.get("entry_type", "issue"),
                importance=entry_payload.get("importance", "main"),
                label=entry_payload.get("label"),
                note=entry_payload.get("note"),
                is_optional=entry_payload.get("is_optional", False),
            )
            db.add(entry)
            reading_path_entries_count += 1

    issue_matches_synced = sync_issue_matches(db)
    sync_series_matches(db)
    db.commit()
    return CurationSyncResult(
        publishers_synced=len(publishers),
        events_synced=len(events),
        story_arcs_synced=len(story_arcs),
        canonical_series_synced=len(canonical_series),
        canonical_issues_synced=canonical_issue_count,
        reading_paths_synced=len(payload.get("reading_paths", [])),
        reading_path_entries_synced=reading_path_entries_count,
        issue_matches_synced=issue_matches_synced,
    )


def match_local_issue(db: Session, issue: Issue) -> int:
    return sync_issue_matches(db, issue_ids=[issue.id])


def match_local_series(db: Session, series: Series) -> int:
    return sync_series_matches(db, series_ids=[series.id])
