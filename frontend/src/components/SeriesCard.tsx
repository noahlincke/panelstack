import { Link } from 'react-router-dom';
import type { Series } from '../api/types';

type SeriesCardProps = {
  series: Series;
};

export function SeriesCard({ series }: SeriesCardProps) {
  const latestPublishedLabel = series.latestPublishedOn
    ? new Date(series.latestPublishedOn).toLocaleDateString(undefined, { year: 'numeric', month: 'short' })
    : null;

  return (
    <Link to={`/series/${series.id}`} className="series-card">
      <div className={`series-card__art ${series.accentClass}`}>
        {series.coverUrl ? (
          <img src={series.coverUrl} alt={`${series.title} cover`} className="series-card__image" loading="lazy" />
        ) : null}
      </div>
      <div className="series-card__body">
        <div className="series-card__meta">
          <span>{series.publisher}</span>
          <span>{latestPublishedLabel ? `Latest ${latestPublishedLabel}` : series.yearStarted}</span>
        </div>
        <h3>{series.title}</h3>
        <p>{series.synopsis}</p>
        <div className="tag-row">
          {series.tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
        </div>
      </div>
    </Link>
  );
}
