import { Link } from 'react-router-dom';
import type { EventSummary } from '../api/types';

type EventCardProps = {
  event: EventSummary;
};

export function EventCard({ event }: EventCardProps) {
  return (
    <Link to={`/events/${event.id}`} className="event-card">
      <div className="event-card__header">
        <div>
          <p className="eyebrow">{event.publisher}</p>
          <h3>{event.title}</h3>
        </div>
        <span className="path-card__count">{event.years}</span>
      </div>
      <p>{event.description}</p>
      <div className="tag-row">
        {event.pathCount ? <span className="tag">{event.pathCount} paths</span> : null}
        {event.arcCount ? <span className="tag">{event.arcCount} arcs</span> : null}
        {event.sourceName ? <span className="tag">{event.sourceName}</span> : null}
      </div>
    </Link>
  );
}
