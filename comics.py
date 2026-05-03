#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus, unquote, urljoin, urlparse

try:
    import requests
    import urllib3
    from bs4 import BeautifulSoup
except ModuleNotFoundError as exc:
    missing = exc.name or "a required dependency"
    print(
        f"Missing Python dependency: {missing}\n\n"
        "Install downloader dependencies first:\n"
        "  uv venv\n"
        "  uv pip install -r requirements.txt\n\n"
        "Or with the standard library venv:\n"
        "  python3 -m venv .venv\n"
        "  . .venv/bin/activate\n"
        "  pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
DEFAULT_HOST_PREFERENCE = [
    "comicfiles.ru",
    "pixeldrain.com",
    "mediafire.com",
    "ufile.io",
    "uploadfiles.io",
    "mega.nz",
    "terabox.com",
    "1024terabox.com",
    "rootz.so",
    "vikingfile.com",
    "zippyshare.com",
]
ARCHIVE_EXTENSIONS = (".cbz", ".zip", ".cbr", ".rar", ".pdf")
DOWNLOAD_TEXT_HINTS = {
    "download now",
    "download",
    "pixeldrain",
    "mediafire",
    "mega",
    "terabox",
    "rootz",
    "vikingfile",
    "ufile",
    "zippyshare",
}
DEFAULT_OUTPUT_DIR = "~/Documents/panelstack-downloads"
GETCOMICS_SEARCH_URL = "https://getcomics.org/?s={query}"


class ComicDownloadError(RuntimeError):
    pass


@dataclass
class DownloadCandidate:
    label: str
    url: str

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc.lower()


@dataclass
class DownloadResult:
    post_title: str
    source_url: str
    selected_link: DownloadCandidate
    resolved_url: str
    archive_path: Path
    extracted_dir: Path | None


@dataclass
class DownloadPlan:
    post_title: str
    source_url: str
    selected_link: DownloadCandidate
    resolved_url: str


@dataclass
class SearchCandidate:
    title: str
    url: str


def supports_interaction() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def terminal_width(default: int = 88) -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return default


def print_rule(title: str = "") -> None:
    width = min(terminal_width(), 100)
    if title:
        label = f" {title.strip()} "
        side = max(0, (width - len(label)) // 2)
        print(f"{'-' * side}{label}{'-' * max(0, width - side - len(label))}")
    else:
        print("-" * width)


def print_banner() -> None:
    print_rule()
    print("getcomics-dl")
    print("Download a GetComics post, mirror link, direct archive, or search title.")
    print_rule()


def ask_text(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default or ""


def ask_yes_no(prompt: str, default: bool) -> bool:
    marker = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{marker}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def candidate_display(candidate: DownloadCandidate) -> str:
    label = re.sub(r"\s+", " ", candidate.label).strip()
    if label and label.lower() != candidate.host:
        return f"{candidate.host} - {label}"
    return candidate.host or candidate.url


def choose_candidate(candidates: list[DownloadCandidate]) -> DownloadCandidate | None:
    print_rule("Mirrors")
    for index, candidate in enumerate(candidates, start=1):
        print(f"{index:>2}. {candidate_display(candidate)}")
    print(" A. Auto-pick first supported mirror")

    while True:
        value = input("Choose mirror [A]: ").strip().lower()
        if not value or value == "a":
            return None
        if value.isdigit() and 1 <= int(value) <= len(candidates):
            return candidates[int(value) - 1]
        print("Choose a number from the list, or A for auto.")


def build_session(insecure: bool) -> requests.Session:
    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://getcomics.org/",
        }
    )
    session.verify = not insecure
    return session


def fetch(session: requests.Session, url: str, *, stream: bool = False) -> requests.Response:
    try:
        response = session.get(url, timeout=60, allow_redirects=True, stream=stream)
    except requests.exceptions.SSLError as exc:
        raise ComicDownloadError(
            f"SSL verification failed for {url}. Re-run with --insecure if needed."
        ) from exc
    except requests.RequestException as exc:
        raise ComicDownloadError(f"Request failed for {url}: {exc}") from exc

    return response


def ensure_success(response: requests.Response, url: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ComicDownloadError(f"HTTP {response.status_code} while fetching {url}") from exc


def looks_like_archive_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(ARCHIVE_EXTENSIONS)


def normalize_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\s.\-()]+", "", name, flags=re.ASCII).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "comic"


def content_disposition_filename(response: requests.Response) -> str | None:
    header = response.headers.get("content-disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', header, flags=re.I)
    if not match:
        return None
    return normalize_filename(unquote(match.group(1)).strip())


def filename_from_url(url: str) -> str | None:
    path = Path(unquote(urlparse(url).path))
    if not path.name:
        return None
    return normalize_filename(path.name)


def looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_search_text(value: str) -> str:
    normalized = value.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def parse_search_candidates(raw_html: str, base_url: str) -> list[SearchCandidate]:
    soup = BeautifulSoup(raw_html, "html.parser")
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()
    for article in soup.find_all("article"):
        link = article.select_one("h1.post-title a, h2.post-title a, .post-title a")
        if not link or not link.get("href"):
            continue
        url = urljoin(base_url, str(link["href"]))
        title = link.get_text(" ", strip=True)
        if not title or url in seen:
            continue
        seen.add(url)
        candidates.append(SearchCandidate(title=title, url=url))
    return candidates


def score_search_candidate(query: str, candidate: SearchCandidate) -> tuple[int, int, int]:
    normalized_query = normalize_search_text(query)
    normalized_title = normalize_search_text(candidate.title)
    query_tokens = set(normalized_query.split())
    title_tokens = set(normalized_title.split())
    overlap = len(query_tokens & title_tokens)
    score = overlap * 10
    if normalized_query and normalized_query in normalized_title:
        score += 120
    if normalized_title and normalized_title in normalized_query:
        score += 80
    if "tpb" in query_tokens and "tpb" in title_tokens:
        score += 20
    if "omnibus" in title_tokens and "omnibus" not in query_tokens:
        score -= 20
    return (score, overlap, -len(candidate.title))


def resolve_getcomics_search(session: requests.Session, query: str) -> str:
    search_url = GETCOMICS_SEARCH_URL.format(query=quote_plus(query.strip()))
    response = fetch(session, search_url)
    ensure_success(response, search_url)
    candidates = parse_search_candidates(response.text, response.url)
    if not candidates:
        raise ComicDownloadError(f"No GetComics search results found for: {query}")
    best = max(candidates, key=lambda candidate: score_search_candidate(query, candidate))
    return best.url


def infer_filename(post_title: str, response: requests.Response, resolved_url: str) -> str:
    filename = content_disposition_filename(response) or filename_from_url(resolved_url)
    if filename:
        return filename

    parsed = urlparse(resolved_url)
    ext = Path(parsed.path).suffix or ".bin"
    return normalize_filename(post_title) + ext


def parse_post_title(soup: BeautifulSoup, fallback_url: str) -> str:
    heading = soup.find("h1")
    if heading and heading.get_text(strip=True):
        return heading.get_text(" ", strip=True)

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if title:
        return title.split(" - ")[0].strip()

    slug = Path(urlparse(fallback_url).path).name.replace("-", " ").strip()
    return slug or "comic"


def extract_candidates(soup: BeautifulSoup, page_url: str) -> list[DownloadCandidate]:
    candidates: list[DownloadCandidate] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"].strip())
        text = anchor.get_text(" ", strip=True).lower()
        host = urlparse(href).netloc.lower()

        if not href.startswith(("http://", "https://")):
            continue
        if "read online" in text:
            continue

        host_match = any(preferred in host for preferred in DEFAULT_HOST_PREFERENCE)
        text_match = any(hint in text for hint in DOWNLOAD_TEXT_HINTS)
        direct_archive = looks_like_archive_url(href)

        if not (host_match or text_match or direct_archive):
            continue
        if href in seen:
            continue

        seen.add(href)
        label = anchor.get_text(" ", strip=True) or host or href
        candidates.append(DownloadCandidate(label=label, url=href))

    return candidates


def sort_candidates(candidates: Iterable[DownloadCandidate], preferred_host: str | None) -> list[DownloadCandidate]:
    preferred_host = preferred_host.lower() if preferred_host else None

    def score(candidate: DownloadCandidate) -> tuple[int, int, str]:
        host = candidate.host
        if preferred_host and preferred_host in host:
            return (0, 0, host)
        for index, domain in enumerate(DEFAULT_HOST_PREFERENCE, start=1):
            if domain in host:
                return (1, index, host)
        if looks_like_archive_url(candidate.url):
            return (2, 0, host)
        return (3, 0, host)

    return sorted(candidates, key=score)


def resolve_first_supported(
    session: requests.Session,
    candidates: list[DownloadCandidate],
) -> tuple[DownloadCandidate, str]:
    last_error: ComicDownloadError | None = None
    for candidate in candidates:
        try:
            return candidate, resolve_candidate_url(session, candidate)
        except ComicDownloadError as exc:
            last_error = exc

    message = str(last_error) if last_error else "No supported mirrors were usable."
    raise ComicDownloadError(message)


def is_known_mirror_host(host: str) -> bool:
    return any(domain in host for domain in DEFAULT_HOST_PREFERENCE)


def resolve_pixeldrain(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"u", "file"}:
        return f"https://pixeldrain.com/api/file/{parts[1]}"
    return url


def resolve_mediafire(session: requests.Session, url: str) -> str:
    response = fetch(session, url)
    ensure_success(response, url)
    soup = BeautifulSoup(response.text, "html.parser")
    button = soup.select_one("a#downloadButton") or soup.find("a", href=re.compile(r"/download/"))
    if button and button.get("href"):
        return button["href"]
    raise ComicDownloadError("MediaFire page did not expose a direct download button.")


def resolve_ufile(session: requests.Session, url: str) -> str:
    response = fetch(session, url)
    ensure_success(response, url)
    html = response.text

    if "Premium Access Only" in html or "File retrieval required" in html:
        raise ComicDownloadError(
            "The Ufile mirror is archived or premium-only. Pick another mirror with --host."
        )

    soup = BeautifulSoup(html, "html.parser")
    button = soup.find("a", class_=re.compile(r"download-button"))
    if button and button.get("href") and button["href"] != "javascript:void(0)":
        return urljoin(response.url, button["href"])

    match = re.search(r'"(https://[^"]+/download/[^"]+)"', html)
    if match:
        return match.group(1)

    raise ComicDownloadError("Could not resolve a direct Ufile download link.")


def resolve_getcomics_redirect(session: requests.Session, url: str) -> str:
    try:
        response = session.get(url, timeout=60, allow_redirects=False, stream=False)
    except requests.RequestException as exc:
        raise ComicDownloadError(f"Request failed for {url}: {exc}") from exc

    if response.status_code in {301, 302, 303, 307, 308}:
        location = response.headers.get("location")
        if not location:
            raise ComicDownloadError("GetComics redirect wrapper did not include a target URL.")
        redirected_url = urljoin(url, location)
        return resolve_candidate_url(session, DownloadCandidate(label="GetComics redirect", url=redirected_url))

    ensure_success(response, url)
    if response.url != url:
        return resolve_candidate_url(session, DownloadCandidate(label="GetComics redirect", url=response.url))

    raise ComicDownloadError("GetComics redirect wrapper did not lead to a supported mirror.")


def resolve_candidate_url(session: requests.Session, candidate: DownloadCandidate) -> str:
    host = candidate.host

    if looks_like_archive_url(candidate.url):
        return candidate.url
    if host == "getcomics.org" and urlparse(candidate.url).path.startswith("/dls/"):
        return resolve_getcomics_redirect(session, candidate.url)
    if "pixeldrain.com" in host:
        return resolve_pixeldrain(candidate.url)
    if "mediafire.com" in host:
        return resolve_mediafire(session, candidate.url)
    if "ufile.io" in host or "uploadfiles.io" in host:
        return resolve_ufile(session, candidate.url)
    if any(domain in host for domain in ("mega.nz", "terabox.com", "1024terabox.com", "rootz.so", "vikingfile.com")):
        raise ComicDownloadError(
            f"{candidate.label} ({candidate.host}) needs a browser-style flow that this script does not automate yet."
        )
    raise ComicDownloadError(f"Unsupported mirror host: {candidate.host}")


def download_file(
    session: requests.Session,
    resolved_url: str,
    output_dir: Path,
    post_title: str,
    show_progress: bool,
) -> Path:
    response = fetch(session, resolved_url, stream=True)
    ensure_success(response, resolved_url)

    filename = infer_filename(post_title, response, resolved_url)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / filename

    total = int(response.headers.get("content-length") or 0)
    written = 0
    last_paint = 0.0

    if destination.exists() and total > 0 and destination.stat().st_size == total:
        return destination

    with destination.open("wb") as file_handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                file_handle.write(chunk)
                written += len(chunk)
                now = time.monotonic()
                if show_progress and (now - last_paint > 0.08 or written == total):
                    print_progress(written, total)
                    last_paint = now

    if show_progress:
        print_progress(written, total, done=True)

    return destination


def print_progress(written: int, total: int, done: bool = False) -> None:
    if total > 0:
        width = 26
        filled = min(width, int(width * written / total))
        bar = "#" * filled + "." * (width - filled)
        percent = int(100 * written / total)
        message = f"\r[{bar}] {percent:>3}% {written / 1024 / 1024:,.1f}/{total / 1024 / 1024:,.1f} MB"
    else:
        message = f"\rDownloaded {written / 1024 / 1024:,.1f} MB"
    print(message, end="\n" if done else "", flush=True)


def extract_archive(archive_path: Path, output_dir: Path) -> Path | None:
    suffix = archive_path.suffix.lower()
    if suffix not in {".cbz", ".zip"}:
        return None

    target_dir = output_dir / archive_path.stem
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(target_dir)
    return target_dir


def image_files_in(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    )


def maybe_open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def inspect_download(
    source_url: str,
    session: requests.Session,
    preferred_host: str | None,
    output_dir: Path,
    extract: bool,
    open_result: bool,
    choose_mirror: bool = False,
    dry_run: bool = False,
    show_progress: bool = False,
) -> DownloadResult:
    plan = resolve_download_plan(
        source_url=source_url,
        session=session,
        preferred_host=preferred_host,
        choose_mirror=choose_mirror,
    )

    if dry_run:
        return DownloadResult(
            post_title=plan.post_title,
            source_url=plan.source_url,
            selected_link=plan.selected_link,
            resolved_url=plan.resolved_url,
            archive_path=Path(),
            extracted_dir=None,
        )

    archive_path = download_file(session, plan.resolved_url, output_dir, plan.post_title, show_progress=show_progress)
    extracted_dir = extract_archive(archive_path, output_dir) if extract else None

    if open_result:
        maybe_open_path(extracted_dir or archive_path)

    return DownloadResult(
        post_title=plan.post_title,
        source_url=source_url,
        selected_link=plan.selected_link,
        resolved_url=plan.resolved_url,
        archive_path=archive_path,
        extracted_dir=extracted_dir,
    )


def resolve_download_plan(
    source_url: str,
    session: requests.Session,
    preferred_host: str | None,
    choose_mirror: bool = False,
) -> DownloadPlan:
    source_url = source_url.strip()
    if not looks_like_url(source_url):
        source_url = resolve_getcomics_search(session, source_url)

    source_host = urlparse(source_url).netloc.lower()

    if looks_like_archive_url(source_url):
        selected = DownloadCandidate(label="Direct archive", url=source_url)
        resolved_url = source_url
        post_title = filename_from_url(source_url) or "comic"
    elif is_known_mirror_host(source_host):
        selected = DownloadCandidate(label=source_host, url=source_url)
        resolved_url = resolve_candidate_url(session, selected)
        post_title = filename_from_url(resolved_url) or filename_from_url(source_url) or "comic"
    else:
        response = fetch(session, source_url)
        ensure_success(response, source_url)
        soup = BeautifulSoup(response.text, "html.parser")
        post_title = parse_post_title(soup, source_url)
        candidates = sort_candidates(extract_candidates(soup, response.url), preferred_host)
        if not candidates:
            raise ComicDownloadError("No download mirrors were found on the page.")

        selected_choice = choose_candidate(candidates) if choose_mirror else None
        if selected_choice is not None:
            selected = selected_choice
            resolved_url = resolve_candidate_url(session, selected)
        else:
            selected, resolved_url = resolve_first_supported(session, candidates)

    return DownloadPlan(
        post_title=post_title,
        source_url=source_url,
        selected_link=selected,
        resolved_url=resolved_url,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download comics from GetComics posts or direct mirror/archive URLs."
    )
    parser.add_argument("url", nargs="?", help="GetComics post URL or direct archive/mirror URL")
    parser.add_argument(
        "--host",
        help="Prefer a specific mirror host, for example comicfiles.ru or pixeldrain.com",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Where downloaded files should be written (default: %(default)s)",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="Do not extract .cbz/.zip archives after download",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the downloaded archive or extracted folder in the default app",
    )
    parser.add_argument(
        "--choose",
        action="store_true",
        help="Show supported mirrors and choose one interactively",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the selected mirror but do not download anything",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Open the interactive terminal downloader",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL verification for misconfigured mirrors",
    )
    return parser.parse_args()


def run_tui(args: argparse.Namespace) -> int:
    print_banner()
    default_url = args.url if args.url else None
    url = ask_text("Search or URL", default_url)
    if not url:
        print("No search or URL provided.", file=sys.stderr)
        return 1

    print()
    print("Press Enter to accept defaults.")
    output_dir_text = ask_text("Download folder", args.output_dir)
    print("Options: download, open, choose, dry-run, archive-only")
    option_text = ask_text("Options", "download")
    option_tokens = {token.strip().lower() for token in re.split(r"[,\\s]+", option_text) if token.strip()}
    dry_run = args.dry_run or bool(option_tokens & {"dry", "dry-run", "inspect"})
    choose = args.choose or bool(option_tokens & {"choose", "mirror", "mirrors"})
    open_result = args.open or bool(option_tokens & {"open"})
    extract = not args.no_extract and "archive-only" not in option_tokens and "no-extract" not in option_tokens
    host = args.host

    args.url = url
    args.output_dir = output_dir_text
    args.host = host
    args.no_extract = not extract
    args.choose = choose
    args.dry_run = dry_run
    args.open = open_result

    return run_download(args, interactive=True)


def print_result(result: DownloadResult, dry_run: bool = False) -> None:
    print_rule("Result")
    print(f"Title:    {result.post_title}")
    print(f"Mirror:   {candidate_display(result.selected_link)}")
    print(f"Source:   {result.selected_link.url}")
    print(f"Resolved: {result.resolved_url}")

    if dry_run:
        print("Mode:     dry run, no file downloaded")
        return

    print(f"Archive:  {result.archive_path}")

    if result.extracted_dir:
        pages = image_files_in(result.extracted_dir)
        print(f"Extract:  {result.extracted_dir}")
        print(f"Pages:    {len(pages)} image files")
        if pages:
            print(f"First:    {pages[0]}")


def run_download(args: argparse.Namespace, interactive: bool = False) -> int:
    url = args.url or input("Search or URL: ").strip()
    if not url:
        print("No URL provided.", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir).expanduser().resolve()
    session = build_session(args.insecure)
    show_progress = supports_interaction() and not args.dry_run

    try:
        result = inspect_download(
            source_url=url,
            session=session,
            preferred_host=args.host,
            output_dir=output_dir,
            extract=not args.no_extract,
            open_result=args.open,
            choose_mirror=args.choose and supports_interaction(),
            dry_run=args.dry_run,
            show_progress=show_progress,
        )
    except ComicDownloadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if interactive:
            print("Tip: try --host pixeldrain.com, --host mediafire.com, or --insecure for broken TLS mirrors.")
        return 1

    print_result(result, dry_run=args.dry_run)
    return 0


def main() -> int:
    args = parse_args()
    if args.tui or not args.url:
        return run_tui(args)
    return run_download(args)


if __name__ == "__main__":
    raise SystemExit(main())
