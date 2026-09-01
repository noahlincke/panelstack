import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiClient } from '../api/client';
import { formatBytes } from '../lib/formatBytes';
import type {
  AppSettings,
  FlightPrepEstimate,
  FlightPrepQueue,
  FlightPrepTarget,
  ReadingPath,
  ReadingPathDetail,
} from '../api/types';

const POLL_INTERVAL_MS = 1500;

type SelectionKey = string;

function keyFor(target: FlightPrepTarget): SelectionKey {
  return `${target.readingPathId}:${target.entryId}`;
}

export function FlightPrepPage() {
  const [settings, setSettings] = useState<AppSettings | undefined>();
  const [collections, setCollections] = useState<ReadingPath[]>([]);
  const [openCollection, setOpenCollection] = useState<ReadingPathDetail | undefined>();
  const [selection, setSelection] = useState<Map<SelectionKey, FlightPrepTarget>>(new Map());
  const [destination, setDestination] = useState('');
  const [estimate, setEstimate] = useState<FlightPrepEstimate | undefined>();
  const [queue, setQueue] = useState<FlightPrepQueue | undefined>();
  const [isEstimating, setIsEstimating] = useState(false);
  const [error, setError] = useState('');
  const [isHosted, setIsHosted] = useState(false);

  useEffect(() => {
    apiClient
      .getSettings()
      .then((value) => {
        setSettings(value);
        setDestination(value.downloadRoot);
        setIsHosted(Boolean(value.hostedDeployment));
      })
      .catch(() => setSettings(undefined));
    apiClient.listReadingPaths().then(setCollections).catch(() => setCollections([]));
  }, []);

  useEffect(() => {
    if (isHosted) return undefined;
    let cancelled = false;
    const poll = () => {
      apiClient
        .getFlightPrepQueue()
        .then((value) => {
          if (!cancelled) setQueue(value);
        })
        .catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isHosted]);

  const targets = useMemo(() => [...selection.values()], [selection]);

  const toggleTarget = useCallback((target: FlightPrepTarget) => {
    setSelection((current) => {
      const next = new Map(current);
      const key = keyFor(target);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.set(key, target);
      }
      return next;
    });
    setEstimate(undefined);
  }, []);

  const openCollectionDetail = useCallback((readingPathId: string) => {
    apiClient
      .getReadingPath(readingPathId)
      .then(setOpenCollection)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  const selectWholeCollection = useCallback((detail: ReadingPathDetail) => {
    setSelection((current) => {
      const next = new Map(current);
      detail.entries
        .filter((entry) => !entry.matchedIssue)
        .forEach((entry) => {
          const target: FlightPrepTarget = {
            readingPathId: detail.id,
            entryId: entry.id,
            title: entry.canonicalIssue?.title ?? entry.label ?? `Entry ${entry.id}`,
          };
          next.set(keyFor(target), target);
        });
      return next;
    });
    setEstimate(undefined);
  }, []);

  const runEstimate = useCallback(() => {
    setIsEstimating(true);
    setError('');
    apiClient
      .estimateFlightPrep(targets, destination)
      .then(setEstimate)
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setIsEstimating(false));
  }, [destination, targets]);

  const startQueue = useCallback(() => {
    setError('');
    apiClient
      .startFlightPrep(targets, destination)
      .then(setQueue)
      .catch((cause: Error) => setError(cause.message));
  }, [destination, targets]);

  if (isHosted) {
    return (
      <section className="view view--flight-prep">
        <header className="view__header">
          <h1>Flight prep</h1>
        </header>
        <p className="view__empty">
          Flight prep downloads run on your own machine. The hosted library is browse, preview and
          device-download only.
        </p>
      </section>
    );
  }

  const isRunning = queue?.status === 'running';

  return (
    <section className="view view--flight-prep">
      <header className="view__header">
        <h1>Flight prep</h1>
        <p className="view__lede">
          Pick the issues and collected editions you want on disk, check they fit, then run the queue.
        </p>
      </header>

      {error ? <p className="view__error">{error}</p> : null}

      <div className="flight-prep">
        <div className="flight-prep__picker">
          <h2 className="flight-prep__heading">Collections</h2>
          <ul className="flight-prep__collections">
            {collections.map((collection) => (
              <li key={collection.id}>
                <button
                  type="button"
                  className="flight-prep__collection"
                  aria-pressed={openCollection?.id === collection.id}
                  onClick={() => openCollectionDetail(collection.id)}
                >
                  <span>{collection.title}</span>
                  <span className="flight-prep__collection-count">{collection.totalIssues}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="flight-prep__entries">
          {openCollection ? (
            <>
              <div className="flight-prep__entries-head">
                <h2 className="flight-prep__heading">{openCollection.title}</h2>
                <button type="button" className="text-button" onClick={() => selectWholeCollection(openCollection)}>
                  Select all missing
                </button>
              </div>
              <ul className="flight-prep__entry-list">
                {openCollection.entries.map((entry) => {
                  const target: FlightPrepTarget = {
                    readingPathId: openCollection.id,
                    entryId: entry.id,
                    title: entry.canonicalIssue?.title ?? entry.label ?? `Entry ${entry.id}`,
                  };
                  const inLibrary = Boolean(entry.matchedIssue);
                  return (
                    <li key={entry.id}>
                      <label className={`flight-prep__entry ${inLibrary ? 'flight-prep__entry--owned' : ''}`}>
                        <input
                          type="checkbox"
                          checked={selection.has(keyFor(target))}
                          disabled={inLibrary}
                          onChange={() => toggleTarget(target)}
                        />
                        <span className="flight-prep__entry-title">{target.title}</span>
                        {inLibrary ? <span className="chip">In library</span> : null}
                      </label>
                    </li>
                  );
                })}
              </ul>
            </>
          ) : (
            <p className="view__empty">Choose a collection to pick issues from.</p>
          )}
        </div>

        <aside className="flight-prep__tray card">
          <h2 className="flight-prep__heading">
            Selection
            <span className="flight-prep__badge">{targets.length}</span>
          </h2>

          <label className="flight-prep__field">
            <span>Download to</span>
            <input
              type="text"
              value={destination}
              spellCheck={false}
              onChange={(event) => {
                setDestination(event.target.value);
                setEstimate(undefined);
              }}
            />
          </label>
          {settings ? (
            <button
              type="button"
              className="text-button"
              onClick={() => {
                setDestination(settings.defaultDownloadRoot);
                setEstimate(undefined);
              }}
            >
              Use default folder
            </button>
          ) : null}

          <button
            type="button"
            className="flight-prep__action"
            disabled={targets.length === 0 || isEstimating}
            onClick={runEstimate}
          >
            {isEstimating ? 'Resolving sizes…' : 'Check sizes and space'}
          </button>

          {estimate ? (
            <dl className="flight-prep__summary">
              <div>
                <dt>Total download</dt>
                <dd>{formatBytes(estimate.totalBytes)}</dd>
              </div>
              <div>
                <dt>Free on destination</dt>
                <dd>{formatBytes(estimate.destination.freeBytes)}</dd>
              </div>
              <div>
                <dt>Resolved</dt>
                <dd>
                  {estimate.resolvedCount} of {estimate.targets.length}
                </dd>
              </div>
              {estimate.unavailableCount > 0 ? (
                <div>
                  <dt>No source found</dt>
                  <dd>{estimate.unavailableCount}</dd>
                </div>
              ) : null}
            </dl>
          ) : null}

          {estimate && !estimate.fits ? (
            <p className="flight-prep__warning">
              This selection does not fit in the free space on {estimate.destination.path}.
            </p>
          ) : null}

          <button
            type="button"
            className="flight-prep__action flight-prep__action--primary"
            disabled={targets.length === 0 || isRunning || (estimate ? !estimate.fits : false)}
            onClick={startQueue}
          >
            {isRunning ? 'Queue running…' : `Download ${targets.length || ''}`.trim()}
          </button>
        </aside>
      </div>

      {queue ? (
        <section className="flight-prep__queue card">
          <div className="flight-prep__queue-head">
            <h2 className="flight-prep__heading">
              Queue
              <span className="chip">{queue.status}</span>
            </h2>
            <span className="flight-prep__queue-progress">
              {queue.completedCount} / {queue.totalCount}
            </span>
            {isRunning ? (
              <button type="button" className="text-button" onClick={() => apiClient.cancelFlightPrep().then(setQueue)}>
                Cancel after current
              </button>
            ) : null}
          </div>
          <p className="flight-prep__queue-destination">{queue.destination}</p>
          <ol className="flight-prep__queue-list">
            {queue.items.map((item) => (
              <li className={`flight-prep__queue-item flight-prep__queue-item--${item.status}`} key={`${item.readingPathId}:${item.entryId}`}>
                <span className="flight-prep__queue-status">{item.status}</span>
                <span className="flight-prep__queue-title">{item.title}</span>
                {item.detail ? <span className="flight-prep__queue-detail">{item.detail}</span> : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </section>
  );
}
