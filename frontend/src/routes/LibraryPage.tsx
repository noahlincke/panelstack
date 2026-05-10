import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { apiClient } from '../api/client';
import type { AppSettings, ReadingPath, ReadingPathCover } from '../api/types';
import { ReadingPathPoster } from '../components/ReadingPathPoster';
import { useShellSettingsAction } from '../components/Shell';
import { ViewSettingsDrawer } from '../components/ViewSettingsDrawer';
import { usePersistentPosterSize } from '../lib/usePersistentPosterSize';
const INITIAL_VISIBLE_COUNT = 8;
const LOAD_MORE_COUNT = 8;
const PRELOAD_AHEAD_COUNT = 6;

type LibraryPageProps = {
  refreshToken: number;
  searchQuery: string;
  onLibraryMutated: () => void;
};

export function LibraryPage({ refreshToken, searchQuery, onLibraryMutated }: LibraryPageProps) {
  const [paths, setPaths] = useState<ReadingPath[]>([]);
  const [coversById, setCoversById] = useState<Record<string, ReadingPathCover>>({});
  const [status, setStatus] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [posterSize, setPosterSize] = usePersistentPosterSize('library-poster-size', 130);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<AppSettings | undefined>();
  const [downloadRootDraft, setDownloadRootDraft] = useState('');
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_COUNT);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const deferredSearchQuery = useDeferredValue(searchQuery);
  const openSettings = useCallback(() => {
    setIsSettingsOpen(true);
  }, []);
  const shellSettingsAction = useMemo(
    () => ({ label: 'Open library settings', onClick: openSettings }),
    [openSettings],
  );

  useShellSettingsAction(shellSettingsAction);

  async function handleOpenDownloadsFolder() {
    try {
      const result = await apiClient.openDownloadsFolder();
      setStatus(`Opened ${result.path}`);
    } catch (reason: unknown) {
      setStatus(reason instanceof Error ? reason.message : 'Unable to open the downloads folder.');
    }
  }

  async function loadSettings(mountedRef?: { current: boolean }) {
    try {
      const payload = await apiClient.getSettings();
      if (mountedRef && !mountedRef.current) {
        return;
      }
      setSettings(payload);
      setDownloadRootDraft(payload.downloadRoot);
    } catch (reason: unknown) {
      if (mountedRef && !mountedRef.current) {
        return;
      }
      setStatus(reason instanceof Error ? reason.message : 'Unable to load library settings.');
    }
  }

  async function handleSaveSettings() {
    try {
      setIsSavingSettings(true);
      const payload = await apiClient.updateSettings({ downloadRoot: downloadRootDraft });
      setSettings(payload);
      setDownloadRootDraft(payload.downloadRoot);
      setStatus(`Download folder set to ${payload.downloadRoot}`);
    } catch (reason: unknown) {
      setStatus(reason instanceof Error ? reason.message : 'Unable to save library settings.');
    } finally {
      setIsSavingSettings(false);
    }
  }

  useEffect(() => {
    let mounted = true;
    setStatus('');
    setIsLoading(true);
    apiClient.listReadingPaths('latest_published_desc').then((items) => {
      if (mounted) {
        setPaths(items);
        setIsLoading(false);
      }
    }).catch((reason: unknown) => {
      if (mounted) {
        const detail = reason instanceof Error ? reason.message : 'Unable to load catalog.';
        setStatus(import.meta.env.DEV ? `Unable to reach the backend. ${detail}` : `Unable to load catalog. ${detail}`);
        setIsLoading(false);
      }
    });
    return () => {
      mounted = false;
    };
  }, [refreshToken]);

  useEffect(() => {
    const mounted = { current: true };
    void loadSettings(mounted);
    return () => {
      mounted.current = false;
    };
  }, []);

  const normalizedQuery = deferredSearchQuery.trim().toLowerCase();
  const isGlobalSearch = normalizedQuery.length > 0;
  function matchesSearch(path: ReadingPath): boolean {
    if (!isGlobalSearch) {
      return true;
    }
    const searchable = [
      path.title,
      path.publisher,
      path.sourceName,
      path.latestIssueLabel,
      path.eventTitle,
      ...(path.tags ?? []),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return searchable.includes(normalizedQuery);
  }
  const visiblePaths = paths.filter((path) => {
    if (!matchesSearch(path)) {
      return false;
    }
    if (isGlobalSearch) {
      return true;
    }
    if (path.line === 'event') {
      return false;
    }
    const hasReads = (path.totalIssues - (path.unreadCount ?? path.totalIssues)) > 0;
    return path.isDownloaded || hasReads;
  }).sort((left, right) => {
    const leftUnread = left.unreadCount ?? left.totalIssues;
    const rightUnread = right.unreadCount ?? right.totalIssues;
    const leftIsComplete = Boolean(left.isComplete);
    const rightIsComplete = Boolean(right.isComplete);

    if (leftIsComplete !== rightIsComplete) {
      return leftIsComplete ? 1 : -1;
    }

    const leftLastReadAt = left.lastReadAt ? Date.parse(left.lastReadAt) : 0;
    const rightLastReadAt = right.lastReadAt ? Date.parse(right.lastReadAt) : 0;
    if (leftLastReadAt !== rightLastReadAt) {
      return rightLastReadAt - leftLastReadAt;
    }

    return left.title.localeCompare(right.title);
  });
  const renderedPaths = visiblePaths.slice(0, visibleCount);
  const isHostedDeployment = settings?.hostedDeployment === true;

  useEffect(() => {
    setVisibleCount(Math.min(INITIAL_VISIBLE_COUNT, visiblePaths.length));
  }, [normalizedQuery, visiblePaths.length]);

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || visibleCount >= visiblePaths.length) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry?.isIntersecting) {
          setVisibleCount((current) => Math.min(current + LOAD_MORE_COUNT, visiblePaths.length));
        }
      },
      { rootMargin: '1200px 0px' },
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
    };
  }, [visibleCount, visiblePaths.length]);

  useEffect(() => {
    const preloadTarget = visiblePaths.slice(0, Math.min(visibleCount + PRELOAD_AHEAD_COUNT, visiblePaths.length));
    const missingIds = preloadTarget.map((path) => path.id).filter((id) => !coversById[id]);
    if (missingIds.length === 0) {
      return;
    }

    let cancelled = false;
    apiClient.getReadingPathCovers(missingIds).then((payload) => {
      if (!cancelled) {
        setCoversById((current) => ({ ...current, ...payload }));
      }
    }).catch(() => {
      if (cancelled) {
        return;
      }
    });

    return () => {
      cancelled = true;
    };
  }, [coversById, visibleCount, visiblePaths]);

  return (
    <section className="view view--library">
      <ViewSettingsDrawer
        open={isSettingsOpen}
        title="Library settings"
        onClose={() => setIsSettingsOpen(false)}
        posterSize={posterSize}
        onPosterSizeChange={setPosterSize}
      >
        <section>
          <div className="settings-drawer__section-head">
            <h3>Download folder</h3>
            <p>
              {isHostedDeployment
                ? 'Hosted deployments save downloads on the server.'
                : 'Choose where downloaded issues and MangaPill chapters are saved.'}
            </p>
          </div>
          <div className="settings-field">
            <input
              type="text"
              value={downloadRootDraft}
              placeholder={settings?.defaultDownloadRoot ?? 'Download folder path'}
              onChange={(event) => setDownloadRootDraft(event.target.value)}
              spellCheck={false}
            />
            <div className="settings-field__actions">
              <button
                type="button"
                className="button button--primary"
                onClick={() => void handleSaveSettings()}
                disabled={isSavingSettings || downloadRootDraft.trim().length === 0}
              >
                {isSavingSettings ? 'Saving...' : 'Save Folder'}
              </button>
              {settings ? (
                <button
                  type="button"
                  className="button"
                  onClick={() => setDownloadRootDraft(settings.defaultDownloadRoot)}
                  disabled={isSavingSettings}
                >
                  Use Default
                </button>
              ) : null}
            </div>
          </div>
        </section>
      </ViewSettingsDrawer>

      <div className="catalog-toolbar catalog-toolbar--library">
        <div className="catalog-toolbar__left" />
        {!isHostedDeployment ? (
          <button type="button" className="button" onClick={() => void handleOpenDownloadsFolder()}>
            Open Downloads
          </button>
        ) : null}
      </div>
      {status ? <p className="catalog-detail-status">{status}</p> : null}

      {isLoading && paths.length === 0 ? (
        <div className="empty-state empty-state--loading">Loading your library...</div>
      ) : renderedPaths.length > 0 ? (
        <>
          {isLoading ? <div className="loading-inline-status">Refreshing your library...</div> : null}
          <div className="poster-grid" style={{ '--poster-min-width': `${posterSize}px` } as CSSProperties}>
            {renderedPaths.map((path) => (
              <ReadingPathPoster
                key={path.id}
                path={path}
                cover={coversById[path.id]}
                dimIfComplete
                metaMode="library"
              />
            ))}
          </div>
          <div ref={sentinelRef} className="reading-path-poster-sentinel" aria-hidden="true" />
        </>
      ) : (
        <div className="empty-state">
          {status || (normalizedQuery ? 'No collections match that search.' : 'No read or downloaded collections yet.')}
        </div>
      )}
    </section>
  );
}
