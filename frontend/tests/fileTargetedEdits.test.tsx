import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FileEditReview } from '../src/features/files/FileEditCard';
import { FilesView } from '../src/features/files/FilesView';
import {
  TargetedDocxEditor,
  type TargetedDocument,
} from '../src/features/files/TargetedDocxEditor';
import type { Station } from '../src/hooks/useStation';

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const hash = 'a'.repeat(64);
const file = {
  id: 'master-one',
  filename: 'master.docx',
  extension: '.docx',
  size: 500,
  source: 'uploaded',
  created_at: '2026-10-04T10:00:00Z',
  parse_status: 'ready',
};
const document: TargetedDocument = {
  file_id: file.id,
  filename: file.filename,
  before_sha256: hash,
  scope: 'Preserve tables and document structures.',
  warning: 'Text length can change line wrapping.',
  targets: [
    {
      location: 'word/document.xml:p:0',
      text: 'Signature: 01.10.2026',
      section: 'body',
      in_table: false,
    },
    {
      location: 'word/document.xml:p:1',
      text: 'Package: weekend',
      section: 'body',
      in_table: true,
    },
    {
      location: 'word/header1.xml:p:0',
      text: 'Contract series: REF-1',
      section: 'header',
      in_table: false,
    },
  ],
};
const proposal = {
  id: 'review-one',
  file_id: file.id,
  filename: file.filename,
  plan: 'Change the package only.',
  preview_content: 'Before: Package: weekend\nAfter: Package: three days (TEST)',
  edit_mode: 'targeted_text',
  before_sha256: hash,
  after_sha256: 'b'.repeat(64),
  changes: [
    {
      location: 'word/document.xml:p:1',
      before: 'weekend',
      after: 'three days (TEST)',
      matches: 1,
    },
  ],
};

describe('Targeted DOCX editing', () => {
  it('submits only reviewed spans with the opened original hash and waits for confirmation', async () => {
    const fetcher = vi.fn().mockResolvedValue(json(proposal));
    vi.stubGlobal('fetch', fetcher);
    const proposed = vi.fn();
    const user = userEvent.setup();
    render(<TargetedDocxEditor document={document} onClose={vi.fn()} onProposed={proposed} />);
    expect(screen.getByRole('button', { name: 'Review file changes' })).toBeDisabled();
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Paragraph 1' }),
      document.targets[1].location,
    );
    await user.clear(screen.getByRole('textbox', { name: 'Exact current text 1' }));
    await user.type(screen.getByRole('textbox', { name: 'Exact current text 1' }), 'weekend');
    await user.clear(screen.getByRole('textbox', { name: 'Replacement text 1' }));
    await user.type(
      screen.getByRole('textbox', { name: 'Replacement text 1' }),
      'three days (TEST)',
    );
    await user.click(screen.getByRole('button', { name: 'Review file changes' }));
    await waitFor(() => expect(proposed).toHaveBeenCalledWith(proposal));
    const [path, init] = fetcher.mock.calls[0];
    expect(path).toBe('/api/files/master-one/targeted-edit-proposals');
    expect(JSON.parse(init.body)).toEqual({
      before_sha256: hash,
      plan: 'Update the reviewed contract fields',
      changes: [
        { location: document.targets[1].location, before: 'weekend', after: 'three days (TEST)' },
      ],
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('retains the requested spans when the backend rejects an ambiguous match', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(json({ detail: 'Exactly one match required; found 2.' }, 422)),
    );
    const proposed = vi.fn();
    const user = userEvent.setup();
    render(<TargetedDocxEditor document={document} onClose={vi.fn()} onProposed={proposed} />);
    const after = screen.getByRole('textbox', { name: 'Replacement text 1' });
    await user.clear(after);
    await user.type(after, 'Signature: TEST');
    await user.click(screen.getByRole('button', { name: 'Review file changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('found 2');
    expect(after).toHaveValue('Signature: TEST');
    expect(proposed).not.toHaveBeenCalled();
  });

  it('aborts a dismissed review and ignores a late successful proposal', async () => {
    let resolve!: (response: Response) => void;
    const pending = new Promise<Response>((success) => {
      resolve = success;
    });
    const fetcher = vi.fn().mockReturnValue(pending);
    vi.stubGlobal('fetch', fetcher);
    const proposed = vi.fn();
    const user = userEvent.setup();
    const view = render(
      <TargetedDocxEditor document={document} onClose={vi.fn()} onProposed={proposed} />,
    );
    const after = screen.getByRole('textbox', { name: 'Replacement text 1' });
    await user.clear(after);
    await user.type(after, 'Signature: TEST');
    await user.click(screen.getByRole('button', { name: 'Review file changes' }));
    const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => resolve(json(proposal)));
    expect(proposed).not.toHaveBeenCalled();
  });

  it('shows exact before/after fields, context, reviewed hashes, and replacement download', async () => {
    const user = userEvent.setup();
    render(<FileEditReview proposal={proposal} />);
    expect(screen.getByText('weekend', { exact: true })).toBeVisible();
    expect(screen.getByText('three days (TEST)', { exact: true })).toBeVisible();
    expect(screen.getByText(/1 exact match/)).toBeVisible();
    await user.click(screen.getByText('Reviewed file hashes'));
    expect(screen.getByText(hash)).toBeVisible();
    expect(screen.getByRole('link', { name: 'Download reviewed replacement' })).toHaveAttribute(
      'href',
      '/api/files/edit-proposals/review-one/preview',
    );
  });

  it('creates a named library copy and keeps the master listed', async () => {
    let copied = false;
    const fetcher = vi.fn(async (path: string, init?: RequestInit) => {
      if (path.startsWith('/api/files?'))
        return json(
          copied
            ? [file, { ...file, id: 'copy-one', filename: 'TEST client.docx', source: 'copied' }]
            : [file],
        );
      if (path === '/api/files/master-one/copy') {
        expect(JSON.parse(String(init?.body))).toEqual({ filename: 'TEST client.docx' });
        copied = true;
        return json({ ...file, id: 'copy-one', filename: 'TEST client.docx', source: 'copied' });
      }
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    render(<FilesView station={{ beginChat: vi.fn() } as unknown as Station} />);
    await user.click(await screen.findByRole('button', { name: 'Make a copy of master.docx' }));
    const input = screen.getByRole('textbox', { name: 'Filename for copy' });
    await user.clear(input);
    await user.type(input, 'TEST client.docx');
    await user.click(screen.getByRole('button', { name: 'Make a copy' }));
    expect(await screen.findByRole('link', { name: 'TEST client.docx' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'master.docx' })).toBeVisible();
  });
});
