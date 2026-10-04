import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { GoogleConnections, GoogleSetup } from '../src/features/google/GoogleSetup';
import type { GoogleService, GoogleServiceStatus } from '../src/types';

const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const label = (service: GoogleService) => (service === 'gmail' ? 'Gmail' : 'Calendar');
const status = (
  service: GoogleService,
  connected: boolean,
  configured = true,
): GoogleServiceStatus => ({
  service,
  configured,
  connected,
  message: connected
    ? `${label(service)} account connected.`
    : `Complete ${label(service)} consent.`,
  scopes:
    service === 'gmail'
      ? [
          'https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.compose',
        ]
      : [
          'https://www.googleapis.com/auth/calendar.events',
          'https://www.googleapis.com/auth/calendar.calendarlist.readonly',
        ],
  account: connected
    ? service === 'gmail'
      ? { label: 'Mail owner', email: 'mail-owner@example.com' }
      : { label: 'Calendar owner', calendar_id: 'agenda-owner@example.com' }
    : null,
  migration_required: false,
});

describe('Separate Google connection checks', () => {
  it.each(['gmail', 'calendar'] as const)(
    'uses one caught status request per %s check and reports its own account',
    async (service) => {
      let resolve!: (value: Response) => void;
      const pending = new Promise<Response>((success) => {
        resolve = success;
      });
      const fetcher = vi
        .fn()
        .mockResolvedValueOnce(json(status(service, false)))
        .mockReturnValueOnce(pending);
      vi.stubGlobal('fetch', fetcher);
      const connected = vi.fn();
      const user = userEvent.setup();
      render(<GoogleSetup service={service} onConnected={connected} />);
      await screen.findByText('Desktop credentials imported');
      const button = screen.getByRole('button', { name: `Check ${label(service)} connection` });
      await user.click(button);
      expect(button).toBeDisabled();
      await user.click(button);
      expect(fetcher).toHaveBeenCalledTimes(2);
      await act(async () => resolve(json(status(service, true))));
      expect(screen.getByText(`${label(service)} account connected.`)).toBeVisible();
      expect(
        screen.getByRole('button', { name: `Refresh ${label(service)} status` }),
      ).toBeEnabled();
      expect(screen.getByText(/Connected account:/)).toHaveTextContent(
        service === 'gmail' ? 'mail-owner@example.com' : 'agenda-owner@example.com',
      );
      expect(connected).toHaveBeenCalledExactlyOnceWith(status(service, true));
      expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
        `/api/google/${service}/status`,
        `/api/google/${service}/status`,
      ]);
    },
  );

  it.each([
    ['gmail', false],
    ['gmail', true],
    ['calendar', false],
    ['calendar', true],
  ] as const)(
    'shows a genuine %s refresh failure and allows retry (connected=%s)',
    async (service, initialConnected) => {
      const fetcher = vi
        .fn()
        .mockResolvedValueOnce(json(status(service, initialConnected)))
        .mockRejectedValueOnce(new Error('Google status service is unreachable.'))
        .mockResolvedValueOnce(json(status(service, true)));
      vi.stubGlobal('fetch', fetcher);
      const connected = vi.fn();
      const user = userEvent.setup();
      render(<GoogleSetup service={service} onConnected={connected} />);
      const buttonName = initialConnected
        ? `Refresh ${label(service)} status`
        : `Check ${label(service)} connection`;
      await waitFor(() => expect(screen.getByRole('button', { name: buttonName })).toBeEnabled());
      await user.click(screen.getByRole('button', { name: buttonName }));
      expect(screen.getByRole('alert')).toHaveTextContent('Google status service is unreachable.');
      expect(screen.getByRole('button', { name: buttonName })).toBeEnabled();
      expect(connected).not.toHaveBeenCalled();
      expect(fetcher).toHaveBeenCalledTimes(2);
      await user.click(screen.getByRole('button', { name: buttonName }));
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(screen.getByText(`${label(service)} account connected.`)).toBeVisible();
      expect(connected).toHaveBeenCalledTimes(1);
      expect(fetcher).toHaveBeenCalledTimes(3);
    },
  );
});

describe('Independent Google connection cards', () => {
  it('authorizes only the named service, with its own API steps, scopes and migration notice', async () => {
    const current = {
      gmail: { ...status('gmail', false), migration_required: true },
      calendar: status('calendar', false),
    };
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/api/google/gmail/status') return json(current.gmail);
      if (path === '/api/google/calendar/status') return json(current.calendar);
      if (path === '/api/google/gmail/authorize')
        return json({
          authorization_url: 'https://accounts.google.com/fixture-gmail',
          expires_in: 600,
        });
      throw new Error(`Unexpected API request: ${path}`);
    });
    vi.stubGlobal('fetch', fetcher);
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const user = userEvent.setup();
    render(<GoogleConnections />);
    const gmail = within(screen.getByRole('region', { name: 'Gmail connection' }));
    const calendar = within(screen.getByRole('region', { name: 'Calendar connection' }));
    await waitFor(() =>
      expect(gmail.getByRole('button', { name: 'Authorize Gmail' })).toBeEnabled(),
    );
    expect(gmail.getByText('Enable Gmail API in your project.')).toBeVisible();
    expect(gmail.queryByText(/Enable Google Calendar API/)).not.toBeInTheDocument();
    expect(gmail.getByText(/previous shared Google connection/)).toBeVisible();
    expect(calendar.getByText('Enable Google Calendar API in your project.')).toBeVisible();
    expect(gmail.getByText('https://www.googleapis.com/auth/gmail.compose')).toBeInTheDocument();
    expect(
      gmail.queryByText('https://www.googleapis.com/auth/calendar.events'),
    ).not.toBeInTheDocument();
    await user.click(gmail.getByRole('button', { name: 'Authorize Gmail' }));
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        'https://accounts.google.com/fixture-gmail',
        '_blank',
        'noopener,noreferrer',
      ),
    );
    expect(calendar.getByRole('button', { name: 'Authorize Calendar' })).toBeEnabled();
    expect(fetcher.mock.calls.filter(([path]) => String(path).includes('/calendar/'))).toHaveLength(
      1,
    );
    expect(fetcher.mock.calls.some(([path]) => String(path) === '/api/google/authorize')).toBe(
      false,
    );
    current.gmail = { ...status('gmail', true), migration_required: false };
    await user.click(gmail.getByRole('button', { name: 'Check Gmail connection' }));
    expect(gmail.getByText(/Connected account:/)).toHaveTextContent('mail-owner@example.com');
    expect(calendar.getByRole('button', { name: 'Authorize Calendar' })).toBeEnabled();
    open.mockRestore();
  });

  it('disconnects Gmail while preserving a different Calendar account', async () => {
    const current = { gmail: status('gmail', true), calendar: status('calendar', true) };
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/api/google/gmail/status') return json(current.gmail);
      if (path === '/api/google/calendar/status') return json(current.calendar);
      if (path === '/api/google/gmail/disconnect') {
        current.gmail = status('gmail', false);
        return json({ disconnected: true });
      }
      throw new Error(`Unexpected API request: ${path}`);
    });
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    render(<GoogleConnections />);
    await user.click(await screen.findByRole('button', { name: 'Disconnect Gmail' }));
    expect(await screen.findByRole('button', { name: 'Authorize Gmail' })).toBeEnabled();
    const calendar = within(screen.getByRole('region', { name: 'Calendar connection' }));
    expect(calendar.getByText(/Connected account:/)).toHaveTextContent('agenda-owner@example.com');
    expect(calendar.getByRole('button', { name: 'Disconnect Calendar' })).toBeEnabled();
    expect(fetcher.mock.calls.filter(([path]) => String(path).includes('/calendar/'))).toHaveLength(
      1,
    );
  });

  it('imports one shared Desktop JSON and refreshes both service cards without authorizing either', async () => {
    let configured = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/api/google/credentials') {
        configured = true;
        return json({ configured: true });
      }
      if (path === '/api/google/gmail/status') return json(status('gmail', false, configured));
      if (path === '/api/google/calendar/status')
        return json(status('calendar', false, configured));
      throw new Error(`Unexpected API request: ${path}`);
    });
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    render(<GoogleConnections />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Authorize Gmail' })).toBeDisabled(),
    );
    const credentials = {
      installed: { client_id: 'fixture-client', client_secret: 'fixture-only' },
    };
    const file = new File([JSON.stringify(credentials)], 'desktop.json', {
      type: 'application/json',
    });
    Object.defineProperty(file, 'text', { value: async () => JSON.stringify(credentials) });
    await user.upload(screen.getByLabelText('Desktop OAuth credentials'), file);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Authorize Gmail' })).toBeEnabled(),
    );
    expect(screen.getByRole('button', { name: 'Authorize Calendar' })).toBeEnabled();
    const imports = fetcher.mock.calls.filter(
      ([path]) => String(path) === '/api/google/credentials',
    );
    expect(imports).toHaveLength(1);
    expect(
      fetcher.mock.calls.filter(([path]) => String(path).endsWith('/gmail/status')),
    ).toHaveLength(2);
    expect(
      fetcher.mock.calls.filter(([path]) => String(path).endsWith('/calendar/status')),
    ).toHaveLength(2);
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/authorize'))).toBe(false);
  });
});
