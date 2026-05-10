import { Link } from 'react-router-dom';
import type { ReadingPath, ReadingPathCover } from '../api/types';

type ReadingPathPosterProps = {
  path: ReadingPath;
  cover?: ReadingPathCover;
  dimIfComplete?: boolean;
  metaMode?: 'catalog' | 'library';
};

export function ReadingPathPoster({
  path,
  cover,
  dimIfComplete = false,
  metaMode = 'catalog',
}: ReadingPathPosterProps) {
  const unreadCount = path.unreadCount ?? path.totalIssues;
  const isComplete = dimIfComplete && Boolean(path.isComplete);

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
      </Link>
      <div className="poster-tile__meta">
        {metaMode === 'library' ? (
          <span>{unreadCount} unread</span>
        ) : (
          <span className="poster-tile__count">{path.totalIssues} issues</span>
        )}
      </div>
    </article>
  );
}
