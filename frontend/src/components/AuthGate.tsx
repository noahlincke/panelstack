import { FormEvent, useEffect, useState, type ReactNode } from 'react';
import { AUTH_REQUIRED_EVENT, apiClient } from '../api/client';

type AuthGateProps = {
  children: ReactNode;
};

export function AuthGate({ children }: AuthGateProps) {
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let mounted = true;
    let retryTimer: number | undefined;

    const loadSession = async () => {
      if (!mounted) {
        return;
      }
      try {
        const session = await apiClient.getSession();
        if (!mounted) {
          return;
        }
        setEnabled(session.enabled);
        setAuthenticated(session.authenticated);
        setError('');
        setLoading(false);
      } catch (reason: unknown) {
        if (!mounted) {
          return;
        }
        const message = reason instanceof Error ? reason.message : 'Unable to verify session.';
        setError(message);
        setLoading(true);
        retryTimer = window.setTimeout(() => {
          void loadSession();
        }, 1000);
      }
    };

    const handleAuthRequired = () => {
      if (!mounted) {
        return;
      }
      setEnabled(true);
      setAuthenticated(false);
      setError('');
      setLoading(false);
      setPassword('');
    };

    window.addEventListener(AUTH_REQUIRED_EVENT, handleAuthRequired);
    void loadSession();

    return () => {
      window.removeEventListener(AUTH_REQUIRED_EVENT, handleAuthRequired);
      if (retryTimer !== undefined) {
        window.clearTimeout(retryTimer);
      }
      mounted = false;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const session = await apiClient.login(password);
      setEnabled(session.enabled);
      setAuthenticated(session.authenticated);
      setPassword('');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Login failed.');
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return <div className="empty-state">{error ? 'Connecting to backend...' : 'Checking session...'}</div>;
  }

  if (!enabled || authenticated) {
    return <>{children}</>;
  }

  return (
    <div className="auth-lock">
      <div className="auth-lock__catalog" aria-hidden="true">
        <div className="app-shell">
          <div className="app-shell__backdrop" />
          <header className="topbar">
            <nav className="topbar__nav topbar__nav--minimal" aria-label="Primary">
              <span className="nav-link nav-link--icon" />
              <span className="nav-link nav-link--icon nav-link--active" />
              <span className="nav-link nav-link--icon" />
            </nav>
          </header>
          <main className="page-shell">
            <section className="view">
              <div className="catalog-toolbar">
                <div className="catalog-toolbar__left">
                  <div className="filter-groups">
                    {['DC Comics', 'Marvel', 'Anime'].map((label) => (
                      <div key={label} className="filter-group-card auth-lock__filter">
                        <span className="publisher-filter publisher-filter--brand">{label}</span>
                        <span className="publisher-filter publisher-filter--expand" />
                      </div>
                    ))}
                  </div>
                </div>
                <span className="settings-launcher" />
              </div>
              <div className="poster-grid auth-lock__posters">
                {Array.from({ length: 18 }, (_, index) => (
                  <article key={index} className="poster-tile">
                    <div className="poster-tile__title auth-lock__line" />
                    <div className="poster-tile__media auth-lock__poster" />
                    <div className="poster-tile__meta">
                      <span className="auth-lock__line auth-lock__line--short" />
                    </div>
                  </article>
                ))}
              </div>
            </section>
          </main>
        </div>
      </div>
      <div className="auth-lock__overlay">
        <div className="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-card-title">
          <p className="auth-card__eyebrow">Panel Stack</p>
          <h1 id="auth-card-title">Enter password</h1>
          <form className="auth-form" onSubmit={handleSubmit}>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password"
              autoComplete="current-password"
              aria-label="Password"
              spellCheck={false}
              autoFocus
            />
            <button
              type="submit"
              className="button button--primary"
              disabled={submitting || password.length === 0}
            >
              {submitting ? 'Unlocking...' : 'Unlock'}
            </button>
          </form>
          {error ? <p className="auth-card__error">{error}</p> : null}
        </div>
      </div>
    </div>
  );
}
