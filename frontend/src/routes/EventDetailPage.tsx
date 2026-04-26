import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { apiClient } from '../api/client';
import type { EventDetail } from '../api/types';
import { PathCard } from '../components/PathCard';

export function EventDetailPage() {
  const { eventId = '' } = useParams();
  const [event, setEvent] = useState<EventDetail | undefined>();
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let mounted = true;
    setLoaded(false);
    setError('');
    apiClient
      .getEvent(eventId)
      .then((payload) => {
        if (!mounted) {
          return;
        }
        setEvent(payload);
        setLoaded(true);
      })
      .catch((reason: unknown) => {
        if (!mounted) {
          return;
        }
        setError(reason instanceof Error ? reason.message : 'Unable to load event.');
        setLoaded(true);
      });
    return () => {
      mounted = false;
    };
  }, [eventId]);

  if (!loaded) {
    return <div className="empty-state">Loading event...</div>;
  }

  if (error) {
    return <div className="empty-state">{error}</div>;
  }

  if (!event) {
    return <div className="empty-state">Event not found.</div>;
  }

  return (
    <section className="view">
      <div className="detail-hero detail-hero--event">
        <div className="detail-hero__art detail-hero__art--event" />
        <div className="detail-hero__copy">
          <p className="eyebrow">
            {event.publisher} {event.years}
          </p>
          <h1>{event.title}</h1>
          <p>{event.description}</p>
          <div className="tag-row">
            <span className="tag">{event.readingPaths.length} paths</span>
            <span className="tag">{event.arcs.length} arcs</span>
            {event.sourceName ? <span className="tag">{event.sourceName}</span> : null}
          </div>
          <div className="detail-hero__actions">
            <Link to="/reading-paths" className="button button--primary">
              All reading paths
            </Link>
            {event.sourceUrl ? (
              <a href={event.sourceUrl} target="_blank" rel="noreferrer" className="button">
                Source guide
              </a>
            ) : null}
          </div>
        </div>
      </div>

      <div className="info-grid">
        <article className="info-panel">
          <div className="section-head">
            <h2>Story Arcs</h2>
            <span>{event.arcs.length} tracked</span>
          </div>
          {event.arcs.length > 0 ? (
            <div className="arc-list">
              {event.arcs.map((arc) => (
                <div key={arc.id} className="arc-row">
                  <div>
                    <strong>{arc.title}</strong>
                    <p>{arc.phase ?? 'editorial lane'}</p>
                  </div>
                  <span className="tag">{arc.status}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">No story arcs attached yet.</div>
          )}
        </article>

        <article className="info-panel">
          <div className="section-head">
            <h2>Recommended Paths</h2>
            <span>{event.readingPaths.length} routes</span>
          </div>
          {event.readingPaths.length > 0 ? (
            <div className="reading-paths reading-paths--stacked">
              {event.readingPaths.map((path) => (
                <PathCard key={path.id} path={path} />
              ))}
            </div>
          ) : (
            <div className="empty-state">No reading paths available yet.</div>
          )}
        </article>
      </div>
    </section>
  );
}
