"""OPDS 1.2 catalog feeds.

This is how a phone gets comics off lincke.org without a Mac in the loop: an
OPDS reader such as Panels adds the catalog once, browses the same reading lists
and collections as the web UI, and pulls archives straight from the streaming
download endpoint. Nothing is stored on the host to make that work.

Feeds are Atom XML, so they are built as text rather than through the JSON
response models used everywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.sax.saxutils import escape

# Starlette only adds a charset for text/*, so it is declared here. Without it a
# reader may decode the body as Latin-1 and fail on any non-ASCII title.
NAVIGATION_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation;charset=utf-8"
ACQUISITION_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition;charset=utf-8"
# The link "type" attribute names the feed kind and must stay charset-free.
NAVIGATION_LINK_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQUISITION_LINK_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
IMAGE_REL = "http://opds-spec.org/image"
THUMBNAIL_REL = "http://opds-spec.org/image/thumbnail"

# Panels keys off the media type to decide it can open the file.
ARCHIVE_MEDIA_TYPES = {
    ".cbz": "application/vnd.comicbook+zip",
    ".zip": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".rar": "application/vnd.comicbook-rar",
    ".pdf": "application/pdf",
}
DEFAULT_ARCHIVE_MEDIA_TYPE = "application/vnd.comicbook+zip"


def archive_media_type(filename: str | None) -> str:
    if not filename:
        return DEFAULT_ARCHIVE_MEDIA_TYPE
    lowered = filename.lower()
    for suffix, media_type in ARCHIVE_MEDIA_TYPES.items():
        if lowered.endswith(suffix):
            return media_type
    return DEFAULT_ARCHIVE_MEDIA_TYPE


@dataclass(frozen=True)
class Link:
    href: str
    rel: str
    type: str
    title: str | None = None


@dataclass(frozen=True)
class Entry:
    identifier: str
    title: str
    links: list[Link]
    updated: str | None = None
    summary: str | None = None
    authors: list[str] = field(default_factory=list)


def _tag(name: str, value: str | None) -> str:
    return f"    <{name}>{escape(value)}</{name}>\n" if value else ""


def _link(link: Link) -> str:
    title = f' title="{escape(link.title)}"' if link.title else ""
    return f'    <link rel="{escape(link.rel)}" href="{escape(link.href)}" type="{escape(link.type)}"{title}/>\n'


def _entry(entry: Entry, now: str) -> str:
    parts = ["  <entry>\n"]
    parts.append(_tag("title", entry.title))
    parts.append(_tag("id", entry.identifier))
    parts.append(_tag("updated", entry.updated or now))
    for author in entry.authors:
        parts.append(f"    <author><name>{escape(author)}</name></author>\n")
    if entry.summary:
        parts.append(f'    <content type="text">{escape(entry.summary)}</content>\n')
    parts.extend(_link(link) for link in entry.links)
    parts.append("  </entry>\n")
    return "".join(parts)


def feed(
    *,
    feed_id: str,
    title: str,
    self_href: str,
    start_href: str,
    entries: list[Entry],
    kind: str = "navigation",
    up_href: str | None = None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    feed_type = NAVIGATION_LINK_TYPE if kind == "navigation" else ACQUISITION_LINK_TYPE
    links = [
        Link(href=self_href, rel="self", type=feed_type),
        Link(href=start_href, rel="start", type=NAVIGATION_LINK_TYPE, title="Panel Stack"),
    ]
    if up_href:
        links.append(Link(href=up_href, rel="up", type=NAVIGATION_LINK_TYPE))

    document = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:opds="http://opds-spec.org/2010/catalog" '
        'xmlns:dc="http://purl.org/dc/terms/">\n',
        _tag("id", feed_id),
        _tag("title", title),
        _tag("updated", now),
        "    <author><name>Panel Stack</name></author>\n",
    ]
    document.extend(_link(link) for link in links)
    document.extend(_entry(entry, now) for entry in entries)
    document.append("</feed>\n")
    return "".join(document)


def navigation_entry(
    *,
    identifier: str,
    title: str,
    href: str,
    summary: str | None = None,
    kind: str = "navigation",
) -> Entry:
    """A link to another feed. `kind` tells the reader what it will get."""
    return Entry(
        identifier=identifier,
        title=title,
        summary=summary,
        links=[
            Link(
                href=href,
                rel="subsection",
                type=NAVIGATION_LINK_TYPE if kind == "navigation" else ACQUISITION_LINK_TYPE,
            )
        ],
    )


def acquisition_entry(
    *,
    identifier: str,
    title: str,
    download_href: str,
    media_type: str,
    summary: str | None = None,
    cover_href: str | None = None,
    updated: str | None = None,
) -> Entry:
    links = [Link(href=download_href, rel=ACQUISITION_REL, type=media_type)]
    if cover_href:
        links.append(Link(href=cover_href, rel=IMAGE_REL, type="image/jpeg"))
        links.append(Link(href=cover_href, rel=THUMBNAIL_REL, type="image/jpeg"))
    return Entry(identifier=identifier, title=title, summary=summary, links=links, updated=updated)


def collected_edition_queries(title: str) -> list[str]:
    """Progressively looser searches for a collected edition.

    Publisher subtitles are the least reliable part of a trade's name — the
    catalogue may say "Vol. 2 - The Hunt" where the release is "Vol. 2 -
    Abomination" — so the subtitle is dropped before giving up.
    """
    candidates = [title]
    without_subtitle = title
    for separator in (" - ", " \u2013 ", " \u2014 ", ": "):
        head, found, _ = without_subtitle.partition(separator)
        if found and "vol" in head.lower():
            without_subtitle = head
            break
    if without_subtitle != title:
        candidates.append(f"{without_subtitle} (TPB)")
        candidates.append(without_subtitle)
    stripped = title.replace(" (TPB)", "").strip()
    if stripped and stripped not in candidates:
        candidates.append(stripped)
    seen: set[str] = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]
