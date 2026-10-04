import { ExternalLink, RefreshCw, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { errorMessage, post, request } from '../../api/client';
import { ErrorNotice, Loading } from '../../components/ui';
import { useResource } from '../../hooks/useResource';
import type { GoogleService, GoogleServiceStatus } from '../../types';

const labels = { gmail: 'Gmail', calendar: 'Calendar' };
const loaders = {
  gmail: () => request<GoogleServiceStatus>('/google/gmail/status'),
  calendar: () => request<GoogleServiceStatus>('/google/calendar/status'),
};
function GoogleCredentials({ onImported }: { onImported: () => void | Promise<unknown> }) {
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function importCredentials(input: File | undefined) {
    if (!input) return;
    setBusy(true);
    setError('');
    try {
      await post('/google/credentials', { credentials: JSON.parse(await input.text()) });
      await onImported();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
      if (file.current) file.current.value = '';
    }
  }
  return (
    <div className="google-credentials">
      <ol className="setup-steps">
        <li>
          Open{' '}
          <a href="https://console.cloud.google.com/" target="_blank" rel="noreferrer">
            Google Cloud Console <ExternalLink size={12} />
          </a>{' '}
          and create or select a project.
        </li>
        <li>
          Configure Google Auth Platform. For personal accounts, select External audience and add
          the account you intend to connect as a test user while the app is in Testing.
        </li>
        <li>
          Create an OAuth client with application type <strong>Desktop app</strong>, then download
          its credentials JSON and import it below.
        </li>
      </ol>
      <p className="subtle">
        One Desktop client configures both services. Importing a different client disconnects both
        local connections. Gmail and Calendar require separate authorization and can use different
        accounts.
      </p>
      <p className="subtle">
        External apps in Testing may receive refresh tokens that expire after seven days. Set the
        audience publishing status to In production for a long-lived personal connection. Google may
        show an unverified-app warning for personal use.
      </p>
      <ErrorNotice message={error} />
      <input
        ref={file}
        type="file"
        aria-label="Desktop OAuth credentials"
        accept="application/json,.json"
        hidden
        onChange={(event) => void importCredentials(event.target.files?.[0])}
      />
      <button className="button secondary" disabled={busy} onClick={() => file.current?.click()}>
        <Upload size={16} />
        Import Desktop credentials
      </button>
      {busy ? <Loading text="Importing Desktop credentials…" /> : null}
    </div>
  );
}
export function GoogleConnections() {
  const [revision, setRevision] = useState(0);
  return (
    <>
      <section className="section" aria-label="Shared Google Desktop credentials">
        <h2>Google Desktop credentials</h2>
        <GoogleCredentials onImported={() => setRevision((value) => value + 1)} />
      </section>
      <GoogleSetup key={`gmail-${revision}`} service="gmail" showCredentials={false} />
      <GoogleSetup key={`calendar-${revision}`} service="calendar" showCredentials={false} />
    </>
  );
}
export function GoogleSetup({
  service,
  onConnected,
  showCredentials = true,
}: {
  service: GoogleService;
  onConnected?: (status: GoogleServiceStatus) => void;
  showCredentials?: boolean;
}) {
  const resource = useResource(loaders[service]);
  const label = labels[service];
  const [busy, setBusy] = useState(false);
  const [authUrl, setAuthUrl] = useState('');
  async function action(run: () => Promise<unknown>, refreshStatus = true) {
    setBusy(true);
    resource.setError(null);
    try {
      await run();
      if (refreshStatus) await resource.refresh();
    } catch (err) {
      resource.setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function authorize() {
    await action(async () => {
      const result = await request<{ authorization_url: string }>(`/google/${service}/authorize`);
      setAuthUrl(result.authorization_url);
      window.open(result.authorization_url, '_blank', 'noopener,noreferrer');
    });
  }
  async function refresh() {
    await action(async () => {
      const status = await loaders[service]();
      resource.setData(status);
      if (status.connected) onConnected?.(status);
    }, false);
  }
  const account = resource.data?.account;
  const accountId = account?.email || account?.calendar_id;
  return (
    <section className="section google-setup" aria-label={`${label} connection`}>
      <h2>{label} connection</h2>
      <ErrorNotice message={resource.error} />
      {resource.loading ? (
        <Loading text={`Checking ${label} connection…`} />
      ) : (
        <div className="notice">
          <span>{resource.data?.message}</span>
          <span className="subtle">
            {resource.data?.connected
              ? `${label} connected`
              : resource.data?.configured
                ? 'Desktop credentials imported'
                : 'Desktop credentials required'}
          </span>
        </div>
      )}
      {account ? (
        <p>
          Connected account: <strong>{account.label}</strong>
          {accountId && accountId !== account.label ? ` (${accountId})` : null}
        </p>
      ) : null}
      {resource.data?.migration_required ? (
        <p className="notice">
          A previous shared Google connection requires separate authorization for {label}.
        </p>
      ) : null}
      {resource.data?.connected ? (
        <div className="row-actions">
          <button
            className="button secondary"
            disabled={busy || resource.loading}
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} />
            Refresh {label} status
          </button>
          <button
            className="button secondary"
            disabled={busy || resource.loading}
            onClick={() => void action(() => post(`/google/${service}/disconnect`))}
          >
            Disconnect {label}
          </button>
        </div>
      ) : (
        <>
          {showCredentials ? <GoogleCredentials onImported={resource.refresh} /> : null}
          <ol className="setup-steps">
            <li>
              Enable {service === 'gmail' ? 'Gmail API' : 'Google Calendar API'} in your project.
            </li>
            <li>
              While the app is in Testing, add the account you will use for {label} as a test user.
            </li>
          </ol>
          <p>
            Authorize {label} separately and choose the Google account to use for {label} on the
            account selection screen. This does not connect the other service.
          </p>
          <p className="subtle">
            {service === 'gmail'
              ? 'Gmail requests permission to read threads and create or send reviewed messages. Sending requires confirmation.'
              : 'Calendar requests permission to list calendars and read or manage events. Changes require confirmation.'}
          </p>
          <div className="row-actions">
            <button
              className="button"
              disabled={busy || resource.loading || !resource.data?.configured}
              onClick={() => void authorize()}
            >
              Authorize {label}
            </button>
            <button
              className="button secondary"
              disabled={busy || resource.loading}
              onClick={() => void refresh()}
            >
              <RefreshCw size={16} />
              Check {label} connection
            </button>
          </div>
          {authUrl ? (
            <p className="subtle">
              {label} authorization opened in your browser.{' '}
              <a href={authUrl} target="_blank" rel="noreferrer">
                Open {label} authorization again
              </a>
              , then check the {label} connection.
            </p>
          ) : null}
        </>
      )}
      {resource.data?.scopes.length ? (
        <details className="scopes">
          <summary>{label} requested permissions</summary>
          <ul>
            {resource.data.scopes.map((scope) => (
              <li key={scope}>
                <code>{scope}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
