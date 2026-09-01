import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiClient } from '../api/client';
import { CoverImage } from '../components/CoverImage';
import { ListItemPicker } from '../components/ListItemPicker';
import { formatBytes } from '../lib/formatBytes';
import type {
  AppSettings,
  DownloadEstimate,
  DownloadQueue,
  DownloadTarget,
  ReadingList,
  ReadingListSummary,
} from '../api/types';

const POLL_INTERVAL_MS = 1500;

export function ListsPage() {
  const [settings, setSettings] = useState<AppSettings | undefined>();
  const [lists, setLists] = useState<ReadingListSummary[]>([]);
  const [openList, setOpenList] = useState<ReadingList | undefined>();
  const [selectedItemIds, setSelectedItemIds] = useState<Set<string>>(new Set());
  const [newListName, setNewListName] = useState('');
  const [destination, setDestination] = useState('');
  const [estimate, setEstimate] = useState<DownloadEstimate | undefined>();
  const [queue, setQueue] = useState<DownloadQueue | undefined>();
  const [isEstimating, setIsEstimating] = useState(false);
  const [error, setError] = useState('');
  const isHosted = Boolean(settings?.hostedDeployment);

  const refreshLists = useCallback(
    () => apiClient.listReadingLists().then(setLists).catch(() => setLists([])),
    [],
  );

  useEffect(() => {
    apiClient
      .getSettings()
      .then((value) => {
        setSettings(value);
        setDestination(value.downloadRoot);
      })
      .catch(() => setSettings(undefined));
    void refreshLists();
  }, [refreshLists]);

  useEffect(() => {
    if (isHosted) return undefined;
    let cancelled = false;
    const poll = () => {
      apiClient
        .getDownloadQueue()
        .then((value: DownloadQueue | undefined) => {
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

  const openListById = useCallback((id: string) => {
    apiClient
      .getReadingList(id)
      .then((list) => {
        setOpenList(list);
        setSelectedItemIds(new Set());
        setEstimate(undefined);
      })
      .catch((cause: Error) => setError(cause.message));
  }, []);

  const downloadableItems = useMemo(
    () => (openList?.items ?? []).filter((item) => !item.owned),
    [openList],
  );
  const allSelected = downloadableItems.length > 0 && downloadableItems.every((item) => selectedItemIds.has(item.id));

  const targets: DownloadTarget[] = useMemo(
    () =>
      (openList?.items ?? [])
        .filter((item) => selectedItemIds.has(item.id))
        .map((item) => ({ readingPathId: item.readingPathId, entryId: item.entryId, title: item.title })),
    [openList, selectedItemIds],
  );

  const toggleAll = () => {
    setSelectedItemIds(allSelected ? new Set() : new Set(downloadableItems.map((item) => item.id)));
    setEstimate(undefined);
  };

  const toggleItem = (itemId: string) => {
    setSelectedItemIds((current) => {
      const next = new Set(current);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
    setEstimate(undefined);
  };

  const createList = () => {
    const name = newListName.trim();
    if (!name) return;
    apiClient
      .createReadingList(name)
      .then((list) => {
        setNewListName('');
        setOpenList(list);
        setSelectedItemIds(new Set());
        void refreshLists();
      })
      .catch((cause: Error) => setError(cause.message));
  };

  const addTargets = (newTargets: DownloadTarget[]) => {
    if (!openList) return;
    apiClient
      .addReadingListItems(openList.id, newTargets)
      .then((list) => {
        setOpenList(list);
        void refreshLists();
      })
      .catch((cause: Error) => setError(cause.message));
  };

  const removeItem = (itemId: string) => {
    if (!openList) return;
    apiClient
      .removeReadingListItem(openList.id, itemId)
      .then((list) => {
        setOpenList(list);
        void refreshLists();
      })
      .catch((cause: Error) => setError(cause.message));
  };

  const isRunning = queue?.status === 'running';

  return (
    <section className="view view--lists">
      <header className="view__header">
        <h1>Lists</h1>
        <p className="view__lede">
          Build a list from anywhere in the catalogue, then download all of it or just the parts you want.
        </p>
      </header>

      {error ? <p className="view__error">{error}</p> : null}

      <div className="lists">
        <div className="lists__sidebar">
          <h2 className="lists__heading">Your lists</h2>
          <div className="lists__create">
            <input
              type="text"
              value={newListName}
              placeholder="New list name"
              onChange={(event) => setNewListName(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && createList()}
            />
            <button type="button" className="text-button" onClick={createList}>
              Add
            </button>
          </div>
          <ul className="lists__index">
            {lists.map((list) => (
              <li key={list.id}>
                <button
                  type="button"
                  className="lists__index-item"
                  aria-pressed={openList?.id === list.id}
                  onClick={() => openListById(list.id)}
                >
                  <span>{list.name}</span>
                  <span className="lists__index-count">{list.itemCount}</span>
                </button>
              </li>
            ))}
          </ul>
          {lists.length === 0 ? <p className="view__empty">No lists yet. Name one above to start.</p> : null}
        </div>

        <div className="lists__items">
          {openList ? (
            <>
              <div className="lists__items-head">
                <label className="lists__select-all">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    disabled={downloadableItems.length === 0}
                    onChange={toggleAll}
                  />
                  <span>Select all ({downloadableItems.length})</span>
                </label>
                <h2 className="lists__heading">{openList.name}</h2>
                <button
                  type="button"
                  className="text-button"
                  onClick={() =>
                    apiClient.deleteReadingList(openList.id).then(() => {
                      setOpenList(undefined);
                      void refreshLists();
                    })
                  }
                >
                  Delete list
                </button>
              </div>
              <ul className="lists__item-list">
                {openList.items.map((item) => (
                  <li key={item.id} className="lists__item">
                    <label className={`lists__item-label ${item.owned ? 'lists__item-label--owned' : ''}`}>
                      <input
                        type="checkbox"
                        checked={selectedItemIds.has(item.id)}
                        disabled={item.owned}
                        onChange={() => toggleItem(item.id)}
                      />
                      <span className="lists__item-cover">
                        <CoverImage src={item.coverUrl} alt="" placeholderLabel="" className="lists__item-image" />
                      </span>
                      <span className="lists__item-title">{item.title}</span>
                      {item.owned ? <span className="chip">In library</span> : null}
                    </label>
                    <button
                      type="button"
                      className="lists__item-remove"
                      aria-label={`Remove ${item.title}`}
                      onClick={() => removeItem(item.id)}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
              {openList.items.length === 0 ? (
                <p className="view__empty">Nothing here yet. Search below to add issues.</p>
              ) : null}

              <ListItemPicker
                onAdd={addTargets}
                existingEntryIds={new Set(openList.items.map((item) => item.entryId))}
              />
            </>
          ) : (
            <p className="view__empty">Pick a list to see what is in it.</p>
          )}
        </div>

        <aside className="lists__tray card">
          <h2 className="lists__heading">
            Download
            <span className="lists__badge">{targets.length}</span>
          </h2>

          {isHosted ? (
            <p className="lists__hosted-note">
              Downloads run on your own machine. The hosted library is browse, preview and device-download only.
            </p>
          ) : (
            <>
              <label className="lists__field">
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
                className="lists__action"
                disabled={targets.length === 0 || isEstimating}
                onClick={() => {
                  setIsEstimating(true);
                  setError('');
                  apiClient
                    .estimateDownloads(targets, destination)
                    .then(setEstimate)
                    .catch((cause: Error) => setError(cause.message))
                    .finally(() => setIsEstimating(false));
                }}
              >
                {isEstimating ? 'Resolving sizes…' : 'Check sizes and space'}
              </button>

              {estimate ? (
                <dl className="lists__summary">
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
                </dl>
              ) : null}

              {estimate && !estimate.fits ? (
                <p className="lists__warning">
                  This selection does not fit in the free space on {estimate.destination.path}.
                </p>
              ) : null}

              <button
                type="button"
                className="lists__action lists__action--primary"
                disabled={targets.length === 0 || isRunning || (estimate ? !estimate.fits : false)}
                onClick={() => {
                  setError('');
                  apiClient
                    .startDownloads(targets, destination)
                    .then(setQueue)
                    .catch((cause: Error) => setError(cause.message));
                }}
              >
                {isRunning ? 'Queue running…' : `Download ${targets.length || ''}`.trim()}
              </button>
            </>
          )}
        </aside>
      </div>

      {queue ? (
        <section className="lists__queue card">
          <div className="lists__queue-head">
            <h2 className="lists__heading">
              Queue
              <span className="chip">{queue.status}</span>
            </h2>
            <span className="lists__queue-progress">
              {queue.completedCount} / {queue.totalCount}
            </span>
            {isRunning ? (
              <button type="button" className="text-button" onClick={() => apiClient.cancelDownloads().then(setQueue)}>
                Cancel after current
              </button>
            ) : null}
          </div>
          <p className="lists__queue-destination">{queue.destination}</p>
          <ol className="lists__queue-list">
            {queue.items.map((item) => (
              <li className={`lists__queue-item lists__queue-item--${item.status}`} key={`${item.readingPathId}:${item.entryId}`}>
                <span className="lists__queue-status">{item.status}</span>
                <span className="lists__queue-title">{item.title}</span>
                {item.detail ? <span className="lists__queue-detail">{item.detail}</span> : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </section>
  );
}
