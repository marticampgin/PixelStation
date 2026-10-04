import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApprovalCard } from '../src/features/google/ApprovalCard';
import type { Approval } from '../src/types';

const reviewedEmail = {
  to: 'recipient@example.com',
  subject: 'Exact subject',
  body: 'First line\nSecond line',
  thread_id: 'thread-one',
  in_reply_to: '<inbound@example.com>',
};
const reviewedEvent = {
  calendar_id: 'primary',
  event_id: 'event-one',
  event: { summary: 'Reviewed event', start: { date: '2026-10-09' }, end: { date: '2026-10-10' } },
  original: {
    summary: 'Original event',
    start: { date: '2026-10-08' },
    end: { date: '2026-10-09' },
  },
  etag: '"actual-etag"',
};

describe('Approval account review', () => {
  it.each([
    ['gmail_send', 'gmail', 'Mail owner', reviewedEmail],
    ['calendar_update', 'calendar', 'Calendar owner', reviewedEvent],
  ] as const)(
    'shows the bound account and preserves exact %s action fields without internal metadata',
    async (action, service, accountLabel, reviewed) => {
      const binding = Object.freeze({
        service,
        generation: 'fixture-generation',
        account: Object.freeze({ label: accountLabel }),
      });
      const payload = Object.freeze({ ...reviewed, connection_binding: binding });
      const original = JSON.stringify(payload);
      const fetcher = vi.fn(
        async () =>
          new Response(JSON.stringify({ result: { id: 'fixture-result' } }), {
            headers: { 'Content-Type': 'application/json' },
          }),
      );
      vi.stubGlobal('fetch', fetcher);
      const complete = vi.fn();
      const user = userEvent.setup();
      const { container } = render(
        <ApprovalCard
          approval={{
            id: 'bound-proposal',
            action,
            payload,
            expires_at: '2026-10-04T12:00:00Z',
          }}
          onComplete={complete}
        />,
      );
      expect(screen.getByText(/Account:/)).toHaveTextContent(`Account: ${accountLabel}`);
      const displayed = container.querySelector('pre')!.textContent!;
      expect(JSON.parse(displayed)).toEqual(reviewed);
      expect(displayed).not.toContain('connection_binding');
      expect(displayed).not.toContain('generation');
      expect(displayed).not.toContain('service');
      expect(screen.queryByText(/Reconnect the service/)).not.toBeInTheDocument();
      expect(fetcher).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Confirm action' }));
      await screen.findByText('Action completed');
      expect(fetcher).toHaveBeenCalledExactlyOnceWith(
        '/api/integrations/approvals/bound-proposal/confirm',
        expect.objectContaining({ method: 'POST', body: '{"confirmed":true}' }),
      );
      expect(complete).toHaveBeenCalledTimes(1);
      expect(JSON.stringify(payload)).toBe(original);
      expect(payload.connection_binding).toBe(binding);
      expect(screen.queryByRole('button', { name: 'Confirm action' })).not.toBeInTheDocument();
    },
  );

  it.each([
    undefined,
    { service: 'gmail', account: { label: 'Unbound legacy label' } },
    { service: 'gmail', generation: 'fixture-generation', account: null },
  ])(
    'requires a new account-bound proposal when the legacy binding has no verified identity',
    (binding) => {
      const payload: Approval['payload'] = { ...reviewedEmail };
      if (binding !== undefined) payload.connection_binding = binding;
      const { container } = render(
        <ApprovalCard
          approval={{
            id: 'legacy-proposal',
            action: 'gmail_send',
            payload,
            expires_at: '2026-10-04T12:00:00Z',
          }}
        />,
      );
      expect(screen.getByText(/Account not recorded/)).toHaveTextContent(
        'Reconnect the service and review a new proposal',
      );
      expect(screen.queryByText('Unbound legacy label')).not.toBeInTheDocument();
      expect(JSON.parse(container.querySelector('pre')!.textContent!)).toEqual(reviewedEmail);
    },
  );
});
