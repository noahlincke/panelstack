import { Link } from 'react-router-dom';
import type { ReadingPath } from '../api/types';

type PathCardProps = {
  path: ReadingPath;
};

function formatPathDate(value?: string): string {
  if (!value) {
    return 'Undated';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short' });
}

export function PathCard({ path }: PathCardProps) {
  const contextualEyebrow = [path.publisher, path.line === 'series' ? 'Series path' : path.line]
    .filter((value): value is string => Boolean(value))
    .join(' · ');
  const eyebrow = path.eventTitle ?? (contextualEyebrow || (path.totalIssues > 0 ? 'Series path' : 'Curated path'));
  const seriesCount = path.seriesCount ?? path.seriesIds.length;

  return (
    <Link to={`/reading-paths/${path.id}`} className="path-card">
      <div className="path-card__header">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h3>{path.title}</h3>
        </div>
        <span className="path-card__count">
          {path.totalIssues > 0 ? `${path.totalIssues} issues` : 'Open path'}
        </span>
      </div>
      <p>{path.description}</p>
      <div className="path-card__footer">
        <span>{path.latestPublishedOn ? `Latest: ${formatPathDate(path.latestPublishedOn)}` : path.estimate}</span>
        <span>
          {seriesCount > 0 ? `${seriesCount} series included` : path.sourceName ?? 'Source-backed'}
        </span>
      </div>
    </Link>
  );
}
