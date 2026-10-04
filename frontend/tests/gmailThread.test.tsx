import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { GmailView } from '../src/features/gmail/GmailView';
import type { EmailMessage, EmailThread } from '../src/types';

const threads: EmailThread[] = [
  {
    id: 'first',
    subject: 'First thread',
    from: 'first@example.com',
    snippet: 'First summary',
    date: '2026-10-04',
  },
  {
    id: 'second',
    subject: 'Second thread',
    from: 'second@example.com',
    snippet: 'Second summary',
    date: '2026-10-04',
  },
];
const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
function deferred() {
  let resolve!: (value: Response) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Response>((success, failure) => {
    resolve = success;
    reject = failure;
  });
  return { promise, resolve, reject };
}
function messages(id: string): EmailMessage[] {
  const inbound: EmailMessage = {
    id: `${id}-inbound`,
    subject: `${id} subject`,
    from: `${id}@example.com`,
    reply_to: `Reply desk <${id}-reply@example.com>`,
    to: 'owner@example.com',
    message_id: `<${id}-inbound@example.com>`,
    label_ids: ['INBOX'],
    body: `${id} inbound email.`,
    date: '2026-10-04',
    attachments: [],
  };
  return [
    inbound,
    {
      ...inbound,
      id: `${id}-sent`,
      from: 'owner@example.com',
      to: `${id}@example.com`,
      reply_to: '',
      message_id: `<${id}-sent@example.com>`,
      label_ids: ['SENT'],
      body: `${id} sent email.`,
    },
    {
      ...inbound,
      id: `${id}-draft`,
      from: 'owner@example.com',
      to: 'different@example.com',
      reply_to: '',
      message_id: `<${id}-draft@example.com>`,
      label_ids: ['DRAFT'],
      body: `${id} previous draft.`,
    },
  ];
}
function setup() {
  const pending = new Map(threads.map((thread) => [thread.id, deferred()]));
  const reply = deferred();
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === '/api/google/gmail/status')
      return json({
        service: 'gmail',
        configured: true,
        connected: true,
        message: 'Connected',
        scopes: [],
        account: { label: 'owner@example.com', email: 'owner@example.com' },
        migration_required: false,
      });
    if (path.startsWith('/api/google/gmail/threads?')) return json({ threads });
    if (path.startsWith('/api/google/gmail/threads/'))
      return pending.get(path.split('/').at(-1)!)!.promise;
    if (path === '/api/google/gmail/reply') return reply.promise;
    if (path === '/api/google/gmail/send')
      return json({
        approval: {
          id: 'fixture-approval',
          action: 'gmail_send',
          payload: JSON.parse(String(init?.body)),
          expires_at: '2026-10-04T12:00:00Z',
        },
      });
    throw new Error(`Unexpected API request: ${path}`);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<GmailView />);
  return { pending, reply, fetcher, user: userEvent.setup() };
}
async function select(user: ReturnType<typeof userEvent.setup>, title: string) {
  await user.click(await screen.findByRole('button', { name: new RegExp(title) }));
}

describe('Gmail thread requests', () => {
  it('ignores a late thread result and keeps the current spinner, recipient and reply headers together', async () => {
    const { user, pending, fetcher } = setup();
    await select(user, 'First thread');
    await select(user, 'Second thread');
    const firstRequest = fetcher.mock.calls.find(([path]) =>
      String(path).endsWith('/threads/first'),
    );
    expect(firstRequest?.[1]?.signal?.aborted).toBe(true);
    await act(async () => pending.get('first')!.resolve(json({ messages: messages('first') })));
    expect(screen.getByRole('heading', { level: 2, name: 'Second thread' })).toBeVisible();
    expect(screen.queryByText('first inbound email.')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'To' })).toHaveValue('');
    expect(screen.getByText('Working with Gmail…')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Generate reply' })).toBeDisabled();
    await act(async () => pending.get('second')!.resolve(json({ messages: messages('second') })));
    expect(screen.getByRole('textbox', { name: 'To' })).toHaveValue('second-reply@example.com');
    expect(screen.getByRole('textbox', { name: 'Subject' })).toHaveValue('Re: Second thread');
    await user.type(screen.getByRole('textbox', { name: 'Draft' }), 'Current reviewed reply.');
    await user.click(screen.getByRole('button', { name: 'Review send' }));
    const proposed = fetcher.mock.calls.find(([path]) => String(path) === '/api/google/gmail/send');
    expect(JSON.parse(String(proposed?.[1]?.body))).toEqual({
      to: 'second-reply@example.com',
      subject: 'Re: Second thread',
      body: 'Current reviewed reply.',
      thread_id: 'second',
      in_reply_to: '<second-inbound@example.com>',
    });
  });

  it('ignores an obsolete detail error without clearing the current reply body', async () => {
    const { user, pending } = setup();
    await select(user, 'First thread');
    await select(user, 'Second thread');
    await act(async () => pending.get('second')!.resolve(json({ messages: messages('second') })));
    await user.type(
      screen.getByRole('textbox', { name: 'Draft' }),
      'A reply for the second thread.',
    );
    await act(async () => pending.get('first')!.reject(new Error('Obsolete first-thread error.')));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Draft' })).toHaveValue(
      'A reply for the second thread.',
    );
    expect(screen.getByRole('textbox', { name: 'To' })).toHaveValue('second-reply@example.com');
    expect(screen.getByRole('button', { name: 'Review send' })).toBeEnabled();
  });

  it('clears previous messages and draft fields immediately and shows a genuine current fetch error', async () => {
    const { user, pending } = setup();
    await select(user, 'First thread');
    await act(async () => pending.get('first')!.resolve(json({ messages: messages('first') })));
    await user.type(screen.getByRole('textbox', { name: 'Draft' }), 'Previous draft body.');
    await user.type(
      screen.getByRole('textbox', { name: 'Instructions for drafting' }),
      'Previous instructions',
    );
    await select(user, 'Second thread');
    expect(screen.queryByText('first inbound email.')).not.toBeInTheDocument();
    for (const name of ['To', 'Subject', 'Draft', 'Instructions for drafting'])
      expect(screen.getByRole('textbox', { name })).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Review send' })).toBeDisabled();
    await act(async () =>
      pending.get('second')!.reject(new Error('Current thread could not be read.')),
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Current thread could not be read.');
    expect(screen.queryByText('Working with Gmail…')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'To' })).toHaveValue('');
  });

  it('keeps an in-flight generated reply attached to its thread before allowing navigation', async () => {
    const { user, pending, reply } = setup();
    await select(user, 'First thread');
    await act(async () => pending.get('first')!.resolve(json({ messages: messages('first') })));
    await user.click(screen.getByRole('button', { name: 'Generate reply' }));
    expect(screen.getByRole('button', { name: /Second thread/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /Second thread/ }));
    expect(screen.getByRole('heading', { level: 2, name: 'First thread' })).toBeVisible();
    await act(async () => reply.resolve(json({ body: 'Generated for the first thread.' })));
    expect(screen.getByRole('textbox', { name: 'Draft' })).toHaveValue(
      'Generated for the first thread.',
    );
    expect(screen.getByRole('button', { name: /Second thread/ })).toBeEnabled();
    await select(user, 'Second thread');
    expect(screen.getByRole('textbox', { name: 'Draft' })).toHaveValue('');
  });
});
