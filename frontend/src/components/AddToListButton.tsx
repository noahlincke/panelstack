import { useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import type { DownloadTarget, ReadingListSummary } from '../api/types';

type AddToListButtonProps = {
  /** Everything the caller wants added, usually a whole collection. */
  targets: DownloadTarget[];
  label?: string;
};

export function AddToListButton({ targets, label = 'Add to list' }: AddToListButtonProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [lists, setLists] = useState<ReadingListSummary[]>([]);
  const [newListName, setNewListName] = useState('');
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    apiClient.listReadingLists().then(setLists).catch(() => setLists([]));
  }, [isOpen]);

  const addTo = (listId: string, listName: string) => {
    apiClient
      .addReadingListItems(listId, targets)
      .then((list) => {
        setStatus(`Added ${targets.length} to ${listName} (${list.items.length} total).`);
        setIsOpen(false);
      })
      .catch((cause: Error) => setStatus(cause.message));
  };

  const createAndAdd = () => {
    const name = newListName.trim();
    if (!name) return;
    apiClient
      .createReadingList(name)
      .then((list) => addTo(list.id, list.name))
      .catch((cause: Error) => setStatus(cause.message));
  };

  return (
    <div className="add-to-list">
      <button
        type="button"
        className="button button--stacked"
        disabled={targets.length === 0}
        onClick={() => setIsOpen((value) => !value)}
      >
        <span>{label}</span>
      </button>
      {isOpen ? (
        <div className="add-to-list__panel card">
          <div className="add-to-list__create">
            <input
              type="text"
              value={newListName}
              placeholder="New list"
              onChange={(event) => setNewListName(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && createAndAdd()}
            />
            <button type="button" className="text-button" onClick={createAndAdd}>
              Create
            </button>
          </div>
          <ul className="add-to-list__index">
            {lists.map((list) => (
              <li key={list.id}>
                <button type="button" className="add-to-list__option" onClick={() => addTo(list.id, list.name)}>
                  <span>{list.name}</span>
                  <span className="add-to-list__count">{list.itemCount}</span>
                </button>
              </li>
            ))}
          </ul>
          {lists.length === 0 ? <p className="add-to-list__empty">No lists yet.</p> : null}
        </div>
      ) : null}
      {status ? <p className="add-to-list__status">{status}</p> : null}
    </div>
  );
}
