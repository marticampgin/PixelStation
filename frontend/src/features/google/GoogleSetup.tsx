import { ExternalLink, RefreshCw, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { errorMessage, post, request } from '../../api/client';
import { ErrorNotice, Loading } from '../../components/ui';
import { useResource } from '../../hooks/useResource';
import type { GoogleStatus } from '../../types';

const loadGoogle = () => request<GoogleStatus>('/google/status');
export function GoogleSetup({ onConnected }: { onConnected?: () => void }) {
  const resource = useResource(loadGoogle);
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [authUrl, setAuthUrl] = useState('');
  async function action(run: () => Promise<unknown>) {
    setBusy(true);
    resource.setError(null);
    try {
      await run();
      await resource.refresh();
    } catch (err) {
      resource.setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function importCredentials(input: File | undefined) {
    if (!input) return;
    await action(async () =>
      post('/google/credentials', { credentials: JSON.parse(await input.text()) }),
    );
    if (file.current) file.current.value = '';
  }
  async function authorize() {
    await action(async () => {
      const result = await request<{ authorization_url: string }>('/google/authorize');
      setAuthUrl(result.authorization_url);
      window.open(result.authorization_url, '_blank', 'noopener,noreferrer');
    });
  }
  async function refresh() {
    await resource.refresh();
    const status = await loadGoogle();
    if (status.connected) onConnected?.();
  }
  return (
    <div className="google-setup">
      <ErrorNotice message={resource.error} />
      {resource.loading ? (
        <Loading text="Checking Google connection…" />
      ) : (
        <div className="notice">
          <span>{resource.data?.message}</span>
          <span className="subtle">
            {resource.data?.connected
              ? 'Connected'
              : resource.data?.configured
                ? 'Credentials imported'
                : 'Setup required'}
          </span>
        </div>
      )}
      {resource.data?.connected ? (
        <div className="row-actions">
          <button className="button secondary" onClick={() => void refresh()}>
            <RefreshCw size={16} />
            Refresh status
          </button>
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => void action(() => post('/google/disconnect'))}
          >
            Disconnect Google
          </button>
        </div>
      ) : (
        <>
          <ol className="setup-steps">
            <li>
              Open{' '}
              <a href="https://console.cloud.google.com/" target="_blank" rel="noreferrer">
                Google Cloud Console <ExternalLink size={12} />
              </a>{' '}
              and create a project.
            </li>
            <li>Enable Gmail API and Google Calendar API.</li>
            <li>
              Configure Google Auth Platform. For a personal account, select External audience and
              add your account as a test user.
            </li>
            <li>
              Create an OAuth client with application type <strong>Desktop app</strong>, then
              download its credentials JSON.
            </li>
            <li>Import the file below and authorize your account in the browser.</li>
          </ol>
          <p className="subtle">
            External apps in Testing may receive refresh tokens that expire after seven days. Set
            the audience publishing status to In production for a long-lived personal connection.
            Google may show an unverified-app warning for personal use.
          </p>
          <div className="row-actions">
            <input
              ref={file}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={(event) => void importCredentials(event.target.files?.[0])}
            />
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => file.current?.click()}
            >
              <Upload size={16} />
              Import credentials
            </button>
            <button
              className="button"
              disabled={busy || !resource.data?.configured}
              onClick={() => void authorize()}
            >
              Authorize Google
            </button>
            <button className="button secondary" onClick={() => void refresh()}>
              <RefreshCw size={16} />
              Check connection
            </button>
          </div>
          {authUrl ? (
            <p className="subtle">
              Authorization opened in your browser.{' '}
              <a href={authUrl} target="_blank" rel="noreferrer">
                Open authorization again
              </a>
              , then check the connection.
            </p>
          ) : null}
        </>
      )}
      {resource.data?.scopes.length ? (
        <details className="scopes">
          <summary>Requested permissions</summary>
          <ul>
            {resource.data.scopes.map((scope) => (
              <li key={scope}>
                <code>{scope}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
