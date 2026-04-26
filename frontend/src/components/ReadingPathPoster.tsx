import { useState } from 'react';
import { Link } from 'react-router-dom';
import { apiClient } from '../api/client';
import type { ReadingPath, ReadingPathCover } from '../api/types';

type ReadingPathPosterProps = {
  path: ReadingPath;
  cover?: ReadingPathCover;
  onLibraryMutated: () => void;
  dimIfComplete?: boolean;
  metaMode?: 'catalog' | 'library';
};

export function ReadingPathPoster({
  path,
  cover,
  onLibraryMutated,
  dimIfComplete = false,
  metaMode = 'catalog',
}: ReadingPathPosterProps) {
  const [isDownloading, setIsDownloading] = useState(false);
  const [status, setStatus] = useState('');
  const [downloaded, setDownloaded] = useState(Boolean(path.isDownloaded));
  const unreadCount = path.unreadCount ?? path.totalIssues;
  const isComplete = dimIfComplete && Boolean(path.isComplete);

  async function handleDownload() {
    try {
      setIsDownloading(true);
      setStatus('');
      const result = await apiClient.downloadReadingPath(path.id);
      onLibraryMutated();
      setDownloaded(result.downloadedIssueCount > 0 || downloaded);
      setStatus(
        result.downloadedIssueCount > 0
          ? 'Downloaded to My Library.'
          : `No new issues downloaded. ${result.skippedIssueCount} already available or unresolved.`,
      );
    } catch (reason: unknown) {
      setStatus(reason instanceof Error ? reason.message : 'Unable to download this reading path right now.');
    } finally {
      setIsDownloading(false);
    }
  }

  return (
    <article className={`poster-tile poster-tile--path ${isComplete ? 'poster-tile--complete' : ''}`}>
      <h2 className="poster-tile__title">
        <Link to={`/all/${path.id}`}>{path.title}</Link>
      </h2>
      <Link to={`/all/${path.id}`} className={`poster-tile__media ${isComplete ? 'poster-tile__media--complete' : ''}`}>
        {cover?.imageUrl ? (
          <img
            src={cover.imageUrl}
            alt={cover.postTitle ?? path.title}
            className="poster-tile__image"
            loading="lazy"
            decoding="async"
          />
        ) : (
          <div className="poster-tile__placeholder">
            <span>{path.publisher ?? 'Loading cover'}</span>
          </div>
        )}
        {!downloaded ? (
          <button
            type="button"
            className={`poster-tile__download ${isDownloading ? 'poster-tile__download--loading' : ''}`}
            onClick={(event) => {
              event.preventDefault();
              void handleDownload();
            }}
            disabled={isDownloading}
            aria-label={isDownloading ? 'Downloading series' : 'Download series'}
            title={isDownloading ? 'Downloading series' : 'Download series'}
          >
            <span className="poster-tile__download-icon" aria-hidden="true">
              ↓
            </span>
          </button>
        ) : null}
      </Link>
      <div className="poster-tile__meta">
        {metaMode === 'library' ? (
          <span>{unreadCount} unread</span>
        ) : (
          <span className="poster-tile__count">{path.totalIssues} issues</span>
        )}
      </div>
      {status ? <p className="poster-tile__status">{status}</p> : null}
    </article>
  );
}
