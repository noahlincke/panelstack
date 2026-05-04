import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { apiClient } from '../api/client';
import type { AppSettings, ReadingPathDetail } from '../api/types';

type ReadingPathDetailPageProps = {
  onLibraryMutated: () => void;
};

export function ReadingPathDetailPage({ onLibraryMutated }: ReadingPathDetailPageProps) {
  const { readingPathId = '' } = useParams();
  const navigate = useNavigate();
  const [path, setPath] = useState<ReadingPathDetail | undefined>();
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [downloadStatus, setDownloadStatus] = useState('');
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadingEntryId, setDownloadingEntryId] = useState<string | null>(null);
  const [settings, setSettings] = useState<AppSettings | undefined>();

  async function loadReadingPath(currentReadingPathId: string, mountedRef?: { current: boolean }) {
    setLoaded(false);
    setError('');
    setDownloadStatus('');
    try {
      const payload = await apiClient.getReadingPath(currentReadingPathId);
      if (mountedRef && !mountedRef.current) {
        return;
      }
      setPath(payload);
      setLoaded(true);
    } catch (reason: unknown) {
      if (mountedRef && !mountedRef.current) {
        return;
      }
      setError(reason instanceof Error ? reason.message : 'Unable to load reading path.');
      setLoaded(true);
    }
  }

  useEffect(() => {
    const mounted = { current: true };
    void loadReadingPath(readingPathId, mounted);
    return () => {
      mounted.current = false;
    };
  }, [readingPathId]);

  useEffect(() => {
    let mounted = true;
    apiClient.getSettings().then((payload) => {
      if (mounted) {
        setSettings(payload);
      }
    }).catch(() => {
      if (mounted) {
        setSettings(undefined);
      }
    });
    return () => {
      mounted = false;
    };
  }, []);

  const availabilityCount = useMemo(
    () => path?.entries.filter((entry) => entry.matchedIssue).length ?? 0,
    [path],
  );
  const hasCollectedEditions = useMemo(
    () => path?.entries.some((entry) => entry.entryType === 'collection') ?? false,
    [path],
  );
  const localSeriesId = path?.entries.find((entry) => entry.matchedIssue)?.matchedIssue?.seriesId;
  const isHostedDeployment = settings?.hostedDeployment === true;

  async function handleDownload() {
    try {
      setIsDownloading(true);
      setDownloadStatus('');
      const result = await apiClient.downloadReadingPath(readingPathId);
      onLibraryMutated();
      setDownloadStatus(
        result.downloadedIssueCount > 0
          ? 'Downloaded to My Library.'
          : `No new issues were downloaded. ${result.skippedIssueCount} issue${result.skippedIssueCount === 1 ? '' : 's'} were already available or unresolved.`,
      );
      await loadReadingPath(readingPathId);
    } catch (reason: unknown) {
      setDownloadStatus(reason instanceof Error ? reason.message : 'Unable to download this reading path right now.');
    } finally {
      setIsDownloading(false);
    }
  }

  async function refreshReadingPath(currentReadingPathId: string): Promise<ReadingPathDetail | undefined> {
    const payload = await apiClient.getReadingPath(currentReadingPathId);
    setPath(payload);
    return payload;
  }

  async function handleEntryDownload(entryId: string, openWhenDone = false) {
    try {
      const currentEntry = path?.entries.find((entry) => entry.id === entryId);
      const entryNoun = currentEntry?.entryType === 'collection' ? 'Collected edition' : 'Issue';
      setDownloadingEntryId(entryId);
      setDownloadStatus('');
      const result = await apiClient.downloadReadingPathEntry(readingPathId, entryId);
      onLibraryMutated();
      setDownloadStatus(
        result.downloadedIssueCount > 0
          ? `${entryNoun} downloaded to My Library.`
          : `This ${entryNoun.toLowerCase()} is already available locally.`,
      );
      const updatedPath = await refreshReadingPath(readingPathId);
      const updatedEntry = updatedPath?.entries.find((entry) => entry.id === entryId);
      if (openWhenDone && updatedEntry?.matchedIssue) {
        if (await canOpenLocalIssue(updatedEntry.matchedIssue.id)) {
          navigate(`/viewer/${updatedEntry.matchedIssue.id}`);
          return;
        }
        setDownloadStatus(
          `${entryNoun} downloaded to My Library, but this archive is not streamable in the viewer on this server.`,
        );
      }
    } catch (reason: unknown) {
      setDownloadStatus(reason instanceof Error ? reason.message : 'Unable to download this issue right now.');
    } finally {
      setDownloadingEntryId(null);
    }
  }

  async function handleOpenDownloadsFolder() {
    try {
      const result = await apiClient.openDownloadsFolder();
      setDownloadStatus(`Opened ${result.path}`);
    } catch (reason: unknown) {
      setDownloadStatus(reason instanceof Error ? reason.message : 'Unable to open the downloads folder.');
    }
  }

  async function handleDeleteSeries() {
    if (!localSeriesId || !window.confirm('Delete this series from My Library?')) {
      return;
    }
    await apiClient.deleteSeries(localSeriesId);
    setDownloadStatus('Series removed from My Library.');
    onLibraryMutated();
    await loadReadingPath(readingPathId);
  }

  async function handleDeleteIssue(issueId: string) {
    if (!window.confirm('Delete this issue from My Library?')) {
      return;
    }
    await apiClient.deleteIssue(issueId);
    setDownloadStatus('Issue removed from My Library.');
    onLibraryMutated();
    await loadReadingPath(readingPathId);
  }

  async function handleToggleRead(entry: ReadingPathDetail['entries'][number], read: boolean) {
    await apiClient.setReadingPathEntryReadState(readingPathId, entry.id, read);
    setDownloadStatus(`Issue marked as ${read ? 'read' : 'unread'}.`);
    onLibraryMutated();
    await loadReadingPath(readingPathId);
  }

  async function canOpenLocalIssue(issueId: string): Promise<boolean> {
    const issue = await apiClient.getIssue(issueId);
    return Boolean(issue?.pages.length);
  }

  async function canStreamCanonicalIssue(issueId: string): Promise<boolean> {
    const issue = await apiClient.getCanonicalIssue(issueId);
    return Boolean(issue?.pages.length);
  }

  async function openEntry(entry: ReadingPathDetail['entries'][number]) {
    if (downloadingEntryId) {
      return;
    }

    if (entry.matchedIssue) {
      try {
        setDownloadingEntryId(entry.id);
        setDownloadStatus('');
        if (await canOpenLocalIssue(entry.matchedIssue.id)) {
          navigate(`/viewer/${entry.matchedIssue.id}`);
          return;
        }
        setDownloadStatus('This issue is local, but its archive is not streamable in the viewer yet.');
      } catch (reason: unknown) {
        setDownloadStatus(reason instanceof Error ? reason.message : 'Unable to open this issue right now.');
      } finally {
        setDownloadingEntryId(null);
      }
      return;
    }

    if (entry.canonicalIssue && path?.accessMode === 'stream') {
      try {
        setDownloadingEntryId(entry.id);
        setDownloadStatus('');
        if (await canStreamCanonicalIssue(entry.canonicalIssue.id)) {
          navigate(`/viewer/canonical/${entry.canonicalIssue.id}`);
          return;
        }
      } catch {
        // Fall through to saving provider records that cannot stream right now.
      } finally {
        setDownloadingEntryId(null);
      }
    }

    await handleEntryDownload(entry.id, true);
  }

  if (!loaded) {
    return <div className="empty-state">Loading reading path...</div>;
  }

  if (error) {
    return <div className="empty-state">{error}</div>;
  }

  if (!path) {
    return <div className="empty-state">Reading path not found.</div>;
  }

  return (
    <section className="view">
      <div className="catalog-detail-head">
        <div className="catalog-detail-head__copy">
          <p className="eyebrow">Available series</p>
          <h1>{path.title}</h1>
          {path.description ? <p className="catalog-detail-head__description">{path.description}</p> : null}
          <p className="catalog-detail-head__meta">
            {path.totalIssues} issues
            {' · '}
            {availabilityCount} in My Library
            {path.latestPublishedOn ? ` · latest ${path.latestPublishedOn}` : ''}
          </p>
          {path.previousCollectionId || path.nextCollectionId ? (
            <p className="catalog-detail-head__meta">
              {path.previousCollectionId ? <Link to={`/all/${path.previousCollectionId}`}>Previous volume</Link> : null}
              {path.previousCollectionId && path.nextCollectionId ? ' · ' : ''}
              {path.nextCollectionId ? <Link to={`/all/${path.nextCollectionId}`}>Next volume</Link> : null}
            </p>
          ) : null}
        </div>
        <div className="catalog-detail-head__actions">
          <button type="button" className="button button--primary" onClick={handleDownload} disabled={isDownloading}>
            {isDownloading ? 'Downloading...' : 'Download all'}
          </button>
          {localSeriesId ? (
            <button type="button" className="button button--stacked" onClick={() => void handleDeleteSeries()}>
              <TrashIcon />
              <span>Remove All</span>
            </button>
          ) : null}
          <Link to="/all" className="button">
            Back to All
          </Link>
          {!isHostedDeployment ? (
            <button type="button" className="button" onClick={() => void handleOpenDownloadsFolder()}>
              Open Downloads
            </button>
          ) : null}
        </div>
      </div>
      {downloadStatus ? <p className="catalog-detail-status">{downloadStatus}</p> : null}

      <div className="section-head">
        <h2>{hasCollectedEditions ? 'Issues and collected editions' : 'Issues'}</h2>
        <span>
          {path.totalIssues} issue{path.totalIssues === 1 ? '' : 's'}
          {hasCollectedEditions ? ` · ${path.entries.length - path.totalIssues} collected` : ''}
        </span>
      </div>

      <div className="poster-grid poster-grid--issues" style={{ '--poster-min-width': '150px' } as CSSProperties}>
        {path.entries.map((entry) => {
          const matchedIssue = entry.matchedIssue;
          const isRead = Boolean(entry.isRead);
          const isCollection = entry.entryType === 'collection';
          const issueTitle = entry.canonicalIssue?.title ?? entry.label ?? 'Issue';
          const issueSubtitle = `${isCollection ? 'Collected edition' : entry.canonicalIssue ? `#${entry.canonicalIssue.issueNumber}` : 'Issue'}${entry.canonicalIssue?.publishedOn ? ` · ${entry.canonicalIssue.publishedOn}` : ''}`;
          const entryNoun = isCollection ? 'collected edition' : 'issue';
          return (
            <article
              key={entry.id}
              className={`poster-tile poster-tile--issue ${isRead ? 'poster-tile--complete' : ''}`}
            >
              <h3 className="poster-tile__title">
                <button type="button" className="poster-tile__title-button" onClick={() => void openEntry(entry)}>
                  {issueTitle}
                </button>
              </h3>
              {matchedIssue ? (
                <div className={`poster-tile__media ${isRead ? 'poster-tile__media--complete' : ''}`}>
                  <button
                    type="button"
                    className="poster-tile__open"
                    onClick={() => void openEntry(entry)}
                    aria-label={`Open ${issueTitle}`}
                  >
                    <IssueCover
                      src={matchedIssue?.coverUrl ?? entry.coverUrl}
                      alt={`${matchedIssue?.title ?? issueTitle} cover`}
                      title={issueTitle}
                      seriesTitle={entry.canonicalSeries?.title}
                      issueNumber={
                        isCollection ? undefined : (entry.canonicalIssue?.issueNumber ?? matchedIssue?.issueNumber)
                      }
                    />
                  </button>
                  <button
                    type="button"
                    className={`poster-tile__read-toggle ${isRead ? 'poster-tile__read-toggle--active' : ''}`}
                    aria-label={`Mark ${issueTitle} as ${isRead ? 'unread' : 'read'}`}
                    title={isRead ? 'Mark unread' : 'Mark read'}
                    onClick={(event) => {
                      event.preventDefault();
                      void handleToggleRead(entry, !isRead);
                    }}
                  >
                    <span aria-hidden="true">👀</span>
                  </button>
                  <button
                    type="button"
                    className="poster-tile__download"
                    aria-label="Remove issue"
                    title="Remove issue"
                    onClick={(event) => {
                      event.preventDefault();
                      void handleDeleteIssue(matchedIssue.id);
                    }}
                  >
                    <TrashIcon />
                  </button>
                  {downloadingEntryId === entry.id ? <DownloadBadge label={`Preparing ${entryNoun}`} /> : null}
                </div>
              ) : entry.canonicalIssue ? (
                <div className={`poster-tile__media ${isRead ? 'poster-tile__media--complete' : ''}`}>
                  <button
                    type="button"
                    className="poster-tile__open"
                    onClick={() => void openEntry(entry)}
                    aria-label={`Open ${issueTitle}`}
                  >
                    <IssueCover
                      src={entry.coverUrl}
                      alt={`${issueTitle} cover`}
                      title={issueTitle}
                      seriesTitle={entry.canonicalSeries?.title}
                      issueNumber={isCollection ? undefined : entry.canonicalIssue.issueNumber}
                    />
                  </button>
                  <button
                    type="button"
                    className={`poster-tile__read-toggle ${isRead ? 'poster-tile__read-toggle--active' : ''}`}
                    aria-label={`Mark ${issueTitle} as ${isRead ? 'unread' : 'read'}`}
                    title={isRead ? 'Mark unread' : 'Mark read'}
                    onClick={(event) => {
                      event.preventDefault();
                      void handleToggleRead(entry, !isRead);
                    }}
                  >
                    <span aria-hidden="true">👀</span>
                  </button>
                  {downloadingEntryId === entry.id ? <DownloadBadge label={`Downloading ${entryNoun}`} /> : null}
                </div>
              ) : (
                <div className={`poster-tile__media ${isRead ? 'poster-tile__media--complete' : ''}`}>
                  <button
                    type="button"
                    className="poster-tile__open"
                    onClick={() => void openEntry(entry)}
                    aria-label={`Open ${issueTitle}`}
                  >
                    <IssueCover
                      src={entry.coverUrl}
                      alt={`${issueTitle} cover`}
                      title={issueTitle}
                      seriesTitle={entry.canonicalSeries?.title}
                    />
                  </button>
                  <button
                    type="button"
                    className={`poster-tile__read-toggle ${isRead ? 'poster-tile__read-toggle--active' : ''}`}
                    aria-label={`Mark ${issueTitle} as ${isRead ? 'unread' : 'read'}`}
                    title={isRead ? 'Mark unread' : 'Mark read'}
                    onClick={() => void handleToggleRead(entry, !isRead)}
                  >
                    <span aria-hidden="true">👀</span>
                  </button>
                  <button
                    type="button"
                    className={`poster-tile__download ${downloadingEntryId === entry.id ? 'poster-tile__download--loading' : ''}`}
                    aria-label={`Download ${entryNoun}`}
                    title={`Download ${entryNoun}`}
                    onClick={() => void handleEntryDownload(entry.id)}
                    disabled={downloadingEntryId === entry.id}
                  >
                    <span className="poster-tile__download-icon" aria-hidden="true">
                      ↓
                    </span>
                  </button>
                  {downloadingEntryId === entry.id ? <DownloadBadge label={`Downloading ${entryNoun}`} /> : null}
                </div>
              )}
              <div className="poster-tile__meta">
                <span>{issueSubtitle}</span>
                <span>{matchedIssue ? 'In Library' : 'Not downloaded'}</span>
              </div>
              {entry.note ? <p className="poster-tile__status">{entry.note}</p> : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

type IssueCoverProps = {
  src?: string;
  alt: string;
  title: string;
  seriesTitle?: string;
  issueNumber?: string;
};

function IssueCover({ src, alt, title, seriesTitle, issueNumber }: IssueCoverProps) {
  const [hasFailed, setHasFailed] = useState(false);

  useEffect(() => {
    setHasFailed(false);
  }, [src]);

  if (src && !hasFailed) {
    return (
      <img
        src={src}
        alt={alt}
        className="poster-tile__image"
        loading="lazy"
        onError={() => setHasFailed(true)}
      />
    );
  }

  return (
    <div className="poster-tile__placeholder poster-tile__placeholder--issue">
      <div className="poster-tile__placeholder-copy">
        <span className="poster-tile__placeholder-eyebrow">Cover unavailable</span>
        <strong className="poster-tile__placeholder-emphasis">
          {issueNumber ? `#${issueNumber}` : title}
        </strong>
        <span className="poster-tile__placeholder-label">{seriesTitle ?? title}</span>
      </div>
    </div>
  );
}

function DownloadBadge({ label }: { label: string }) {
  return (
    <div className="poster-tile__download-badge" aria-live="polite">
      <span className="poster-tile__download-badge-icon" aria-hidden="true">
        <DownloadTrayIcon />
      </span>
      <span className="sr-only">{label}</span>
    </div>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M8.5 5.5h7M10 3.8h4m-7 3 1 11.2A1.8 1.8 0 0 0 9.8 20h4.4a1.8 1.8 0 0 0 1.8-1.7L17 6.8M10 9.5v6M14 9.5v6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DownloadTrayIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 4.5v9M8.5 10.5L12 14l3.5-3.5M5 16.5h3l1.4 2h5.2l1.4-2H19"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
