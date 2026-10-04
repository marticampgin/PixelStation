import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CalendarView } from '../src/features/calendar/CalendarView';
import { GmailView } from '../src/features/gmail/GmailView';
import { ToolsContext } from '../src/layouts/ToolsContext';
import type { GoogleService, GoogleServiceStatus } from '../src/types';

const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
function serviceStatus(service: GoogleService, connected: boolean): GoogleServiceStatus {
  return {
    service,
    configured: true,
    connected,
    message: `${service} ${connected ? 'ready' : 'needs consent'}`,
    scopes: [],
    account: connected
      ? service === 'gmail'
        ? { label: 'Mail account', email: 'mail@example.com' }
        : { label: 'Calendar account', calendar_id: 'agenda@example.com' }
      : null,
    migration_required: false,
  };
}

describe('Service-specific Google workspace gates', () => {
  it.each(['gmail', 'calendar'] as const)(
    'keeps %s gated until its own connection is present, even if the other service is connected',
    async (service) => {
      let connected = false;
      const fetcher = vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        if (path === `/api/google/${service}/status`)
          return json(serviceStatus(service, connected));
        if (path === '/api/google/status') return json({ connected: true });
        if (path === `/api/google/${service === 'gmail' ? 'calendar' : 'gmail'}/status`)
          return json(serviceStatus(service === 'gmail' ? 'calendar' : 'gmail', true));
        if (path.startsWith('/api/google/gmail/threads?')) return json({ threads: [] });
        if (path === '/api/google/calendar/calendars') return json({ items: [] });
        if (path.startsWith('/api/google/calendar/events?')) return json({ items: [] });
        throw new Error(`Unexpected API request: ${path}`);
      });
      vi.stubGlobal('fetch', fetcher);
      const user = userEvent.setup();
      render(service === 'gmail' ? <GmailView /> : <CalendarView />);
      const name = service === 'gmail' ? 'Gmail' : 'Calendar';
      await waitFor(() =>
        expect(screen.getByRole('button', { name: `Authorize ${name}` })).toBeEnabled(),
      );
      expect(
        fetcher.mock.calls.every(([path]) => String(path) === `/api/google/${service}/status`),
      ).toBe(true);
      connected = true;
      await user.click(screen.getByRole('button', { name: `Check ${name} connection` }));
      expect(
        await screen.findByText(service === 'gmail' ? 'mail@example.com' : 'agenda@example.com'),
      ).toBeVisible();
      expect(screen.queryByRole('button', { name: `Authorize ${name}` })).not.toBeInTheDocument();
      await waitFor(() =>
        expect(
          fetcher.mock.calls.some(([path]) =>
            String(path).startsWith(
              service === 'gmail' ? '/api/google/gmail/threads?' : '/api/google/calendar/events?',
            ),
          ),
        ).toBe(true),
      );
      expect(
        fetcher.mock.calls.filter(([path]) => String(path) === `/api/google/${service}/status`),
      ).toHaveLength(3);
      expect(fetcher.mock.calls.some(([path]) => String(path) === '/api/google/status')).toBe(
        false,
      );
      expect(
        fetcher.mock.calls.some(([path]) =>
          String(path).includes(`/google/${service === 'gmail' ? 'calendar' : 'gmail'}/`),
        ),
      ).toBe(false);
    },
  );
});

describe('Google tool connection summaries', () => {
  it.each([
    ['gmail', 'calendar'],
    ['calendar', 'gmail'],
  ] as const)(
    'reports %s availability independently from disconnected %s',
    async (connectedService, disconnectedService) => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async (input: RequestInfo | URL) => {
          const path = String(input);
          if (path.startsWith('/api/tools')) return json([]);
          if (path === '/api/integrations/approvals') return json({ approvals: [] });
          if (path === '/api/web/status' || path === '/api/images/status')
            return json({
              available: true,
              endpoint: 'http://127.0.0.1',
              message: 'Local provider ready',
            });
          if (path === '/api/google/status')
            return json({
              configured: true,
              connected: false,
              scopes: [],
              message: 'Only one service is connected',
              connections: {
                gmail: serviceStatus('gmail', connectedService === 'gmail'),
                calendar: serviceStatus('calendar', connectedService === 'calendar'),
              },
            });
          throw new Error(`Unexpected API request: ${path}`);
        }),
      );
      render(<ToolsContext busy={false} />);
      await screen.findByText(`${connectedService} ready`);
      const connected = within(
        screen.getByText(connectedService === 'gmail' ? 'Gmail' : 'Calendar', {
          selector: 'strong',
        }).parentElement!,
      );
      const disconnected = within(
        screen.getByText(disconnectedService === 'gmail' ? 'Gmail' : 'Calendar', {
          selector: 'strong',
        }).parentElement!,
      );
      expect(connected.getByText('Available')).toBeVisible();
      expect(disconnected.getByText('Setup required')).toBeVisible();
      expect(disconnected.getByText(`${disconnectedService} needs consent`)).toBeVisible();
      expect(screen.queryByText('Google', { selector: 'strong' })).not.toBeInTheDocument();
    },
  );
});
