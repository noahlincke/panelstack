import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { apiClient } from '../api/client';
import type { Issue, Series } from '../api/types';
import { IssueList } from '../components/IssueList';
import { isIssueRead, setIssueReadState } from '../lib/readingProgress';

export function SeriesDetailPage() {
  const { seriesId = '' } = useParams();
  const navigate = useNavigate();
  const [series, setSeries] = useState<Series | undefined>();
  const [issues, setIssues] = useState<Issue[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string>('');
  const [status, setStatus] = useState<string>('');

  useEffect(() => {
    let mounted = true;
    setLoaded(false);
    setError('');
    Promise.all([apiClient.getSeries(seriesId), apiClient.getSeriesIssues(seriesId)]).then(
      ([seriesResult, issuesResult]) => {
        if (!mounted) {
          return;
        }
        setSeries(seriesResult);
        setIssues(issuesResult);
        setStatus('');
        setLoaded(true);
      },
    ).catch((reason: unknown) => {
      if (!mounted) {
        return;
      }
      setError(reason instanceof Error ? reason.message : 'Unable to load series.');
      setLoaded(true);
    });
    return () => {
      mounted = false;
    };
  }, [seriesId]);

  if (!loaded) {
    return <div className="empty-state">Loading series...</div>;
  }

  if (error) {
    return <div className="empty-state">{error}</div>;
  }

  if (!series) {
    return <div className="empty-state">Series not found.</div>;
  }

  if (series.readingPathId) {
    return <Navigate to={`/all/${series.readingPathId}`} replace />;
  }

  const firstIssue = issues[0];

  async function handleDeleteSeries() {
    if (!series || !window.confirm(`Delete ${series.title} from My Library?`)) {
      return;
    }
    await apiClient.deleteSeries(series.id);
    navigate('/library');
  }

  async function handleDeleteIssue(issue: Issue) {
    if (!window.confirm(`Delete ${issue.title} from My Library?`)) {
      return;
    }
    const result = await apiClient.deleteIssue(issue.id);
    if (result.seriesDeleted) {
      navigate('/library');
      return;
    }
    setIssues((current) => current.filter((item) => item.id !== issue.id));
    setStatus(`Deleted ${issue.title}.`);
  }

  function handleToggleRead(issue: Issue, read: boolean) {
    setIssueReadState(issue.seriesId, issue.id, read);
    setStatus(`${issue.title} marked as ${read ? 'read' : 'unread'}.`);
  }

  return (
    <section className="view">
      <div className="detail-hero">
        <div className="detail-hero__art">
          {series.coverUrl || firstIssue?.coverUrl ? (
            <img
              src={series.coverUrl ?? firstIssue?.coverUrl}
              alt={`${series.title} cover`}
              className="detail-hero__cover"
            />
          ) : null}
        </div>
        <div className="detail-hero__copy">
          <p className="eyebrow">
            {series.publisher} {series.status}
          </p>
          <h1>{series.title}</h1>
          <p>{series.synopsis}</p>
          <div className="tag-row">
            {series.tags.map((tag) => (
              <span key={tag} className="tag">
                {tag}
              </span>
            ))}
          </div>
          <div className="detail-hero__actions">
            {firstIssue ? (
              <Link to={`/viewer/${firstIssue.id}`} className="button button--primary">
                Start reading
              </Link>
            ) : (
              <button type="button" className="button button--primary" disabled>
                Start reading
              </button>
            )}
            <Link to="/library" className="button">
              Back to My Library
            </Link>
            <button type="button" className="button" onClick={() => void handleDeleteSeries()}>
              Delete series
            </button>
          </div>
          {status ? <p className="detail-hero__status">{status}</p> : null}
        </div>
      </div>

      <div className="section-head">
        <h2>Issues</h2>
        <span>{issues.length} loaded</span>
      </div>

      <IssueList
        issues={issues}
        isRead={(issue) => isIssueRead(issue.seriesId, issue.id)}
        onToggleRead={handleToggleRead}
        onDeleteIssue={(issue) => void handleDeleteIssue(issue)}
      />
    </section>
  );
}
