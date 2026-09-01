import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { SettingsIcon } from './SettingsIcon';
import { TopbarSearch } from './TopbarSearch';

type ShellProps = {
  children: ReactNode;
  searchQuery: string;
  onSearchChange: (value: string) => void;
};

type ShellSettingsAction = {
  label: string;
  onClick: () => void;
};

const ShellSettingsContext = createContext<(action: ShellSettingsAction | null) => void>(() => {});

export function useShellSettingsAction(action: ShellSettingsAction | null) {
  const setShellSettingsAction = useContext(ShellSettingsContext);

  useEffect(() => {
    setShellSettingsAction(action);
    return () => {
      setShellSettingsAction(null);
    };
  }, [action, setShellSettingsAction]);
}

const navItems = [
  { to: '/catalogue', label: 'Catalogue', icon: 'catalogue' as const },
  { to: '/chronology', label: 'Chronology', icon: 'chronology' as const },
  { to: '/lists', label: 'Lists', icon: 'lists' as const },
];

function TopbarIcon({ kind }: { kind: 'books' | 'cart' | 'search' | 'catalogue' | 'chronology' | 'lists' }) {
  if (kind === 'lists') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="topbar-icon">
        <rect x="3.6" y="5" width="3.4" height="3.4" rx="1" fill="currentColor" />
        <rect x="3.6" y="10.3" width="3.4" height="3.4" rx="1" fill="currentColor" opacity="0.72" />
        <rect x="3.6" y="15.6" width="3.4" height="3.4" rx="1" fill="currentColor" opacity="0.72" />
        <path
          d="M9.8 6.7h10.6M9.8 12h10.6M9.8 17.3h7.4"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
      </svg>
    );
  }

  if (kind === 'catalogue') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="topbar-icon">
        <rect x="4" y="4.5" width="6.6" height="6.6" rx="1.4" fill="currentColor" />
        <rect x="13.4" y="4.5" width="6.6" height="6.6" rx="1.4" fill="currentColor" opacity="0.72" />
        <rect x="4" y="12.9" width="6.6" height="6.6" rx="1.4" fill="currentColor" opacity="0.72" />
        <rect x="13.4" y="12.9" width="6.6" height="6.6" rx="1.4" fill="currentColor" />
      </svg>
    );
  }

  if (kind === 'chronology') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="topbar-icon">
        <path d="M6 4.5v15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        <circle cx="6" cy="8" r="2.1" fill="currentColor" />
        <circle cx="6" cy="15.4" r="2.1" fill="currentColor" opacity="0.72" />
        <path d="M10.6 8h8.2M10.6 15.4h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }

  if (kind === 'books') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="topbar-icon">
        <rect x="4" y="4.5" width="4" height="15" rx="1.4" fill="currentColor" />
        <rect x="9.4" y="3.5" width="4.2" height="16" rx="1.4" fill="currentColor" opacity="0.94" />
        <rect x="15" y="5" width="4.2" height="14.5" rx="1.4" fill="currentColor" />
        <path d="M5.6 7.3h0.8M11.1 6.3h0.8M16.6 7.6h0.8" stroke="#f7f2eb" strokeWidth="1.2" strokeLinecap="round" />
      </svg>
    );
  }

  if (kind === 'search') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="topbar-icon">
        <circle
          cx="10.5"
          cy="10.5"
          r="4.75"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        />
        <path
          d="M14.1 14.1 18.4 18.4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="topbar-icon">
      <path
        d="M6 6h14l-1.6 7.5a2 2 0 0 1-2 1.5H10a2 2 0 0 1-2-1.6L6.4 5H3V3h4a1 1 0 0 1 1 .8L8.4 5H21v1zM10 21a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm7 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z"
        fill="currentColor"
      />
    </svg>
  );
}

export function Shell({ children, searchQuery, onSearchChange }: ShellProps) {
  const location = useLocation();
  const [isTopbarVisible, setIsTopbarVisible] = useState(true);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [settingsAction, setSettingsAction] = useState<ShellSettingsAction | null>(null);
  const hideTimerRef = useRef<number | null>(null);
  const registerSettingsAction = useCallback((action: ShellSettingsAction | null) => {
    setSettingsAction(action);
  }, []);

  const isViewerRoute = location.pathname.startsWith('/viewer/');

  function clearHideTimer() {
    if (hideTimerRef.current !== null) {
      window.clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
  }

  function scheduleTopbarHide() {
    clearHideTimer();
    if (!isViewerRoute) {
      return;
    }

    hideTimerRef.current = window.setTimeout(() => {
      setIsTopbarVisible(false);
    }, 1800);
  }

  function revealTopbar() {
    setIsTopbarVisible(true);
    scheduleTopbarHide();
  }

  useEffect(() => {
    if (!isViewerRoute) {
      setIsTopbarVisible(true);
      clearHideTimer();
      return;
    }

    revealTopbar();

    function handlePointerMove(event: PointerEvent) {
      if (event.clientY <= 120) {
        revealTopbar();
      }
    }

    function handleTouchStart() {
      revealTopbar();
    }

    function handleNonArrowKeyDown(event: KeyboardEvent) {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        return;
      }
      revealTopbar();
    }

    window.addEventListener('pointermove', handlePointerMove, { passive: true });
    window.addEventListener('touchstart', handleTouchStart);
    window.addEventListener('keydown', handleNonArrowKeyDown, true);

    return () => {
      clearHideTimer();
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('touchstart', handleTouchStart);
      window.removeEventListener('keydown', handleNonArrowKeyDown, true);
    };
  }, [isViewerRoute]);

  return (
    <ShellSettingsContext.Provider value={registerSettingsAction}>
      <div className={isViewerRoute ? 'app-shell app-shell--viewer' : 'app-shell'}>
        <div className="app-shell__backdrop" />
        <header
          className={
            isViewerRoute
              ? `topbar topbar--viewer ${isTopbarVisible ? 'topbar--visible' : 'topbar--hidden'}`
              : 'topbar'
          }
          onMouseEnter={() => {
            if (isViewerRoute) {
              clearHideTimer();
              setIsTopbarVisible(true);
            }
          }}
          onMouseLeave={() => {
            if (isViewerRoute) {
              scheduleTopbarHide();
            }
          }}
        >
          <nav
            className={`topbar__nav topbar__nav--minimal ${isSearchOpen ? 'topbar__nav--searching' : ''}`}
            aria-label="Primary"
          >
            <div className="topbar__spacer" aria-hidden="true" />
            <div className="topbar__center">
              {navItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => (isActive ? 'nav-link nav-link--icon nav-link--active' : 'nav-link nav-link--icon')}
                >
                  <TopbarIcon kind={item.icon} />
                  <span className="sr-only">{item.label}</span>
                </NavLink>
              ))}
              <TopbarSearch
                query={searchQuery}
                onQueryChange={onSearchChange}
                isOpen={isSearchOpen}
                onOpenChange={setIsSearchOpen}
                icon={<TopbarIcon kind="search" />}
              />
            </div>
            <div className="topbar__actions">
              {settingsAction ? (
                <button
                  type="button"
                  className="nav-link nav-link--icon topbar-settings"
                  aria-label={settingsAction.label}
                  onClick={settingsAction.onClick}
                >
                  <SettingsIcon className="topbar-icon" />
                </button>
              ) : null}
            </div>
          </nav>
        </header>

        <main className={isViewerRoute ? 'page-shell page-shell--viewer' : 'page-shell'}>{children}</main>
      </div>
    </ShellSettingsContext.Provider>
  );
}
