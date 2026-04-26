import { Link } from 'react-router-dom';
import type { Series } from '../api/types';
import { getCollectionProgressKey, getUnreadCountForCollection } from '../lib/readingProgress';

type LibraryPosterProps = {
  series: Series;
};

export function LibraryPoster({ series }: LibraryPosterProps) {
  const progressKey = getCollectionProgressKey({ readingPathId: series.readingPathId, seriesId: series.id });
  const unreadCount = getUnreadCountForCollection(progressKey, series.issueCount);
  const isComplete = series.issueCount > 0 && unreadCount === 0;

  return (
    <article className={`poster-tile ${isComplete ? 'poster-tile--complete' : ''}`}>
      <h2 className="poster-tile__title">
        <Link to={`/series/${series.id}`}>{series.title}</Link>
      </h2>
      <Link to={`/series/${series.id}`} className={`poster-tile__media ${isComplete ? 'poster-tile__media--complete' : ''}`}>
        {series.coverUrl ? (
          <img src={series.coverUrl} alt={`${series.title} cover`} className="poster-tile__image" loading="lazy" />
        ) : (
          <div className="poster-tile__placeholder">
            <span>{series.publisher}</span>
          </div>
        )}
      </Link>
      <div className="poster-tile__meta">
        <span>{unreadCount} unread</span>
      </div>
    </article>
  );
}
