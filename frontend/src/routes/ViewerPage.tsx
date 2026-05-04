import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { apiClient } from '../api/client';
import type { Issue } from '../api/types';

type WebkitDocument = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
};

type WebkitElement = HTMLDivElement & {
  webkitRequestFullscreen?: () => Promise<void> | void;
};

export function ViewerPage() {
  const { issueId = '', canonicalIssueId = '' } = useParams();
  const [issue, setIssue] = useState<Issue | undefined>();
  const [pageIndex, setPageIndex] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string>('');
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
      if (event.key === 'f' || event.key === 'F') {
        event.preventDefault();
        void toggleFullscreen();
      }
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
    document.body.classList.add('viewer-fullscreen');
    return () => {
      document.body.classList.remove('viewer-fullscreen');
    };
  }, []);

  async function toggleFullscreen() {
    const stage = stageRef.current as WebkitElement | null;
    const fullscreenDocument = document as WebkitDocument;
    if (!stage) {
      return;
    }

    if (document.fullscreenElement === stage || fullscreenDocument.webkitFullscreenElement === stage) {
      if (document.exitFullscreen) {
        await document.exitFullscreen();
      } else {
        await fullscreenDocument.webkitExitFullscreen?.();
      }
      return;
    }

    try {
      if (stage.requestFullscreen) {
        await stage.requestFullscreen();
        return;
      }
      if (stage.webkitRequestFullscreen) {
        await stage.webkitRequestFullscreen();
        return;
      }
    } catch {
      // Mobile Safari may reject fullscreen requests for non-video elements.
    }
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
      <div
        ref={stageRef}
        className="viewer-stage viewer-stage--fullscreen viewer-stage--immersive"
      >
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

        <div className="viewer-hit-zones" aria-label="Reader controls">
          <button
            type="button"
            className="viewer-hit-zone viewer-hit-zone--back"
            disabled={!canGoBack}
            onClick={goBack}
            aria-label="Previous page"
          >
          </button>
          <button
            type="button"
            className="viewer-hit-zone viewer-hit-zone--forward"
            disabled={!canGoForward}
            onClick={goForward}
            aria-label="Next page"
          >
          </button>
        </div>
      </div>
    </section>
  );
}
