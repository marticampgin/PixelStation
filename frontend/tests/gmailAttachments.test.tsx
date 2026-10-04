import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { GmailView } from '../src/features/gmail/GmailView';
import type { LocalFile } from '../src/types';

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const file: LocalFile = {
  id: 'a'.repeat(32),
  filename: 'Rental agreement.docx',
  source: 'gmail',
  extension: '.docx',
  size: 4096,
  created_at: '2026-10-04',
  media_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  parse_status: 'ready',
};
function setup(failImport = false) {
  const onAnalyze = vi.fn();
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === '/api/google/gmail/status')
      return json({
        service: 'gmail',
        connected: true,
        configured: true,
        account: { label: 'Owner' },
      });
    if (path.startsWith('/api/google/gmail/threads?'))
      return json({
        threads: [
          {
            id: 'thread1',
            subject: 'Rental test',
            from: 'client@example.com',
            snippet: 'Agreement attached',
            date: '2026-10-04',
          },
        ],
      });
    if (path === '/api/google/gmail/threads/thread1')
      return json({
        messages: [
          {
            id: 'message1',
            from: 'client@example.com',
            to: 'owner@example.com',
            subject: 'Rental test',
            date: '2026-10-04',
            body: 'Review the agreement.',
            message_id: '<message1@example.com>',
            attachments: [
              {
                filename: file.filename,
                size: file.size,
                mime_type: file.media_type,
                part_id: '1',
                attachment_id: 'remote-id',
              },
            ],
          },
        ],
      });
    if (path === '/api/google/gmail/templates') return json([]);
    if (path.endsWith('/attachments/1/import'))
      return failImport
        ? json({ detail: { message: 'Attachment content is invalid.' } }, 422)
        : json(file);
    if (path === '/api/files') return json([file]);
    if (path === '/api/google/gmail/drafts')
      return json({ id: 'saved-draft', attachment_count: 1 });
    if (path === '/api/google/gmail/send')
      return json({
        approval: {
          id: 'approval1',
          action: 'gmail_send',
          expires_at: '2099-01-01',
          payload: {
            ...JSON.parse(String(init?.body)),
            attachment_ids: undefined,
            attachments: [
              {
                id: 'private-snapshot-id',
                filename: file.filename,
                media_type: file.media_type,
                size: file.size,
                sha256: 'b'.repeat(64),
              },
            ],
            connection_binding: {
              service: 'gmail',
              generation: 'private-generation',
              account: { label: 'Owner' },
            },
          },
        },
      });
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<GmailView onAnalyze={onAnalyze} />);
  return { fetcher, onAnalyze, user: userEvent.setup() };
}
async function openThread(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /Rental test/ }));
  await screen.findByText('Review the agreement.');
}

describe('Gmail attachment workflows', () => {
  it('offers a message-part download, saves verified bytes to Files and analyzes the imported file', async () => {
    const { user, fetcher, onAnalyze } = setup();
    await openThread(user);
    expect(screen.getByRole('link', { name: `Download ${file.filename}` })).toHaveAttribute(
      'href',
      '/api/google/gmail/messages/message1/attachments/1/content',
    );
    await user.click(screen.getByRole('button', { name: `Save ${file.filename} to Files` }));
    expect(await screen.findByText(`Saved ${file.filename} to Files.`)).toBeVisible();
    await user.click(screen.getByRole('button', { name: `Analyze ${file.filename} in chat` }));
    expect(onAnalyze).toHaveBeenCalledWith(file);
    const calls = fetcher.mock.calls.filter(([path]) => String(path).endsWith('/import'));
    expect(calls).toHaveLength(1);
    expect(calls[0][1]?.body).toBeUndefined();
    expect(fetcher.mock.calls.some(([path]) => String(path).includes('/send'))).toBe(false);
  });

  it('reports an actual failed import and never passes an unavailable file to analysis', async () => {
    const { user, onAnalyze } = setup(true);
    await openThread(user);
    await user.click(screen.getByRole('button', { name: `Analyze ${file.filename} in chat` }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Attachment content is invalid.');
    expect(onAnalyze).not.toHaveBeenCalled();
    expect(screen.queryByText(`Saved ${file.filename} to Files.`)).not.toBeInTheDocument();
  });

  it('includes selected managed IDs in draft/send requests and displays the frozen attachment review', async () => {
    const { user, fetcher } = setup();
    await openThread(user);
    await user.click(screen.getByRole('button', { name: 'Attach from Files' }));
    await user.click(await screen.findByRole('checkbox', { name: /Rental agreement.docx/ }));
    await user.type(screen.getByRole('textbox', { name: 'Draft' }), 'Test attachment for review.');
    await user.click(screen.getByRole('button', { name: 'Create Gmail draft' }));
    expect(await screen.findByText('Gmail draft created: saved-draft')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Review send' }));
    expect(
      await screen.findByRole('region', { name: 'Reviewed email attachments' }),
    ).toHaveTextContent(file.filename);
    expect(screen.getByText(`SHA-256: ${'b'.repeat(64)}`)).toBeVisible();
    expect(screen.queryByText(/private-snapshot-id/)).not.toBeInTheDocument();
    for (const endpoint of ['/drafts', '/send']) {
      const call = fetcher.mock.calls.find(([path]) => String(path).endsWith(endpoint));
      expect(JSON.parse(String(call?.[1]?.body)).attachment_ids).toEqual([file.id]);
    }
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Confirm action' })).toBeEnabled(),
    );
  });
});
