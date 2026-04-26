import { useEffect, type ReactNode } from 'react';

type SortOption = {
  value: string;
  label: string;
  hint?: string;
};

type ViewSettingsDrawerProps = {
  open: boolean;
  title: string;
  sort?: string;
  sortOptions?: readonly SortOption[];
  onClose: () => void;
  onSortChange?: (value: string) => void;
  posterSize: number;
  onPosterSizeChange: (value: number) => void;
  children?: ReactNode;
};

export function ViewSettingsDrawer({
  open,
  title,
  sort,
  sortOptions,
  onClose,
  onSortChange,
  posterSize,
  onPosterSizeChange,
  children,
}: ViewSettingsDrawerProps) {
  useEffect(() => {
    if (!open) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose();
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [open, onClose]);

  return (
    <div className={`settings-drawer ${open ? 'settings-drawer--open' : ''}`} aria-hidden={!open}>
      <button
        type="button"
        className={`settings-drawer__backdrop ${open ? 'settings-drawer__backdrop--visible' : ''}`}
        aria-label="Close settings"
        onClick={onClose}
      />
      <aside className="settings-drawer__panel" aria-label={title}>
        <div className="settings-drawer__head">
          <div>
            <p className="settings-drawer__eyebrow">View</p>
            <h2>{title}</h2>
          </div>
          <button type="button" className="settings-drawer__close" onClick={onClose} aria-label="Close settings">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M6 6l12 12M18 6L6 18"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {children ? <div className="settings-drawer__section">{children}</div> : null}

        {sort && sortOptions && sortOptions.length > 0 && onSortChange ? (
          <section className="settings-drawer__section">
            <div className="settings-drawer__section-head">
              <h3>Sort</h3>
              <p>Choose the order of the grid.</p>
            </div>
            <div className="settings-drawer__options">
              {sortOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`settings-option ${sort === option.value ? 'settings-option--active' : ''}`}
                  onClick={() => onSortChange(option.value)}
                >
                  <span>{option.label}</span>
                  {option.hint ? <small>{option.hint}</small> : null}
                </button>
              ))}
            </div>
          </section>
        ) : null}

        <section className="settings-drawer__section">
          <div className="settings-drawer__section-head">
            <h3>Cover size</h3>
            <p>Adjust how many covers fit on screen.</p>
          </div>
          <label className="settings-slider">
            <input
              type="range"
              min="110"
              max="240"
              step="10"
              value={posterSize}
              onChange={(event) => onPosterSizeChange(Number(event.target.value))}
            />
          </label>
          <div className="settings-slider__labels">
            <span>Dense</span>
            <span>Roomy</span>
          </div>
        </section>
      </aside>
    </div>
  );
}
