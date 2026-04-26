import { useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import type { EventSummary } from '../api/types';
import { EventCard } from '../components/EventCard';

export function EventsPage() {
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;
    setError('');
    apiClient
      .listEvents()
      .then((items) => {
        if (mounted) {
          setEvents(items);
        }
      })
      .catch((reason: unknown) => {
        if (mounted) {
          setError(reason instanceof Error ? reason.message : 'Unable to load events.');
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <section className="view">
      <div className="hero hero--compact">
        <div>
          <p className="eyebrow">Events</p>
          <h1>Continuity anchors for giant universes.</h1>
        </div>
        <p className="hero__copy">
          Start from the event level when the shape of a universe matters more than any single
          issue. These views separate core books, tie-ins, and character lanes.
        </p>
      </div>

      {error ? (
        <div className="empty-state">{error}</div>
      ) : events.length > 0 ? (
        <div className="event-grid">
          {events.map((event) => (
            <EventCard key={event.id} event={event} />
          ))}
        </div>
      ) : (
        <div className="empty-state">No canonical events have been synced yet.</div>
      )}
    </section>
  );
}
