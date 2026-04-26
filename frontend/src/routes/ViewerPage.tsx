import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { apiClient } from '../api/client';
import type { Issue } from '../api/types';

export function ViewerPage() {
  const { issueId = '', canonicalIssueId = '' } = useParams();
  const [issue, setIssue] = useState<Issue | undefined>();
  const [pageIndex, setPageIndex] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string>('');
  const [isFullscreen, setIsFullscreen] = useState(false);
  const stageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let mounted = true;
    setLoaded(false);
    setError('');
    const request = canonicalIssueId ? apiClient.getCanonicalIssue(canonicalIssueId) : apiClient.getIssue(issueId);
    request.then((item) => {
      if (mounted) {
        setIssue(item);
        setPageIndex(0);
        setLoaded(true);
      }
    }).catch((reason: unknown) => {
      if (mounted) {
        setError(reason instanceof Error ? reason.message : 'Unable to load issue.');
        setLoaded(true);
      }
    });
    return () => {
      mounted = false;
    };
  }, [issueId, canonicalIssueId]);

  const currentPage = issue?.pages[pageIndex];
  const canGoBack = pageIndex > 0;
  const canGoForward = pageIndex < (issue?.pages.length ?? 0) - 1;

  useEffect(() => {
    if (!issue) {
      return;
    }
    if (issue.canonicalIssueId && issue.id.startsWith('canonical:')) {
      void apiClient.setCanonicalIssueReadState(issue.canonicalIssueId, true, true);
      return;
    }
    void apiClient.setIssueReadState(issue.id, true, true);
  }, [issue]);

  function goBack() {
    setPageIndex((value) => Math.max(0, value - 1));
  }

  function goForward() {
    setPageIndex((value) => Math.min((issue?.pages.length ?? 1) - 1, value + 1));
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        goBack();
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        goForward();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [issue?.pages.length]);

  useEffect(() => {
    function onFullscreenChange() {
      setIsFullscreen(document.fullscreenElement === stageRef.current);
    }

    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', onFullscreenChange);
    };
  }, []);

  useEffect(() => {
    document.body.classList.toggle('viewer-fullscreen', isFullscreen);
    return () => {
      document.body.classList.remove('viewer-fullscreen');
    };
  }, [isFullscreen]);

  async function toggleFullscreen() {
    if (!stageRef.current) {
      return;
    }

    if (document.fullscreenElement === stageRef.current) {
      await document.exitFullscreen();
      return;
    }

    await stageRef.current.requestFullscreen();
  }

  if (!loaded) {
    return <div className="empty-state">Loading issue...</div>;
  }

  if (error) {
    return <div className="empty-state">{error}</div>;
  }

  if (!issue) {
    return <div className="empty-state">Issue not found.</div>;
  }

  return (
    <section className="view view--viewer">
      <div ref={stageRef} className={isFullscreen ? 'viewer-stage viewer-stage--fullscreen' : 'viewer-stage'}>
        <article className="page-canvas" aria-label={`Page ${pageIndex + 1}`}>
          {currentPage?.imageUrl ? (
            <img
              className="page-canvas__image"
              src={currentPage.imageUrl}
              alt={`${issue.title} page ${pageIndex + 1}`}
              loading="eager"
            />
          ) : issue.pages.length === 0 ? (
            <div className="empty-state">
              This issue is indexed, but the selected archive does not expose streamable page images
              yet.
            </div>
          ) : (
            <div className={`page-canvas__art page-canvas__art--${currentPage?.tone ?? 'bone'}`} />
          )}
        </article>

        {!isFullscreen ? (
          <div className="viewer-toolbar">
            <div className="viewer-controls" aria-label="Page navigation">
              <button
                type="button"
                className="button viewer-button"
                disabled={!canGoBack}
                onClick={goBack}
              >
                ← Previous
              </button>
              <button type="button" className="button viewer-button viewer-button--utility" onClick={toggleFullscreen}>
                Fullscreen
              </button>
              <button
                type="button"
                className="button button--primary viewer-button"
                disabled={!canGoForward}
                onClick={goForward}
              >
                Next →
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
