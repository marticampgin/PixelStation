import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EmailTemplates } from '../src/features/gmail/EmailTemplates';

afterEach(() => vi.unstubAllGlobals());

describe('Reviewed reply templates', () => {
  it('requires review and approval before template selection', async () => {
    const selected = vi.fn();
    let approved = false;
    const fetch = vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init?.method === 'POST') approved = true;
      return new Response(
        JSON.stringify(
          init?.method === 'POST'
            ? {}
            : [
                {
                  id: 'template',
                  name: 'Rental reply',
                  body: 'Thank you for your request.',
                  keywords: ['rental'],
                  approved,
                  review_sha256: 'a'.repeat(64),
                },
              ],
        ),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetch);
    render(<EmailTemplates selectedIds={[]} onSelected={selected} />);
    await userEvent.click(screen.getByText('Reply templates', { exact: true }));
    const choice = await screen.findByRole('checkbox', { name: 'Rental reply · Needs review' });
    expect(choice).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Approve this wording' }));
    const reviewed = fetch.mock.calls.find((call) => call[1]?.method === 'POST');
    expect(JSON.parse(String(reviewed?.[1]?.body))).toEqual({
      confirmed: true,
      review_sha256: 'a'.repeat(64),
    });
    const approvedChoice = await screen.findByRole('checkbox', { name: 'Rental reply · Approved' });
    expect(approvedChoice).toBeEnabled();
    await userEvent.click(approvedChoice);
    expect(selected).toHaveBeenCalledWith(['template']);
  });

  it('editing selected wording removes its current selection and saves for fresh review', async () => {
    const selected = vi.fn();
    const fetch = vi.fn(
      async (_url: unknown, init?: RequestInit) =>
        new Response(
          JSON.stringify(
            init?.method === 'PUT'
              ? {}
              : [
                  {
                    id: 'template',
                    name: 'Rental reply',
                    body: 'Original wording',
                    keywords: ['rental'],
                    approved: true,
                    review_sha256: 'a'.repeat(64),
                  },
                ],
          ),
          { status: 200 },
        ),
    );
    vi.stubGlobal('fetch', fetch);
    render(<EmailTemplates selectedIds={['template']} onSelected={selected} />);
    await userEvent.click(screen.getByText('Reply templates', { exact: true }));
    await userEvent.click(await screen.findByRole('button', { name: 'Edit template' }));
    await userEvent.clear(screen.getByRole('textbox', { name: 'Template wording' }));
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Template wording' }),
      'Changed wording',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save changes for review' }));
    await waitFor(() => expect(selected).toHaveBeenCalledWith([]));
    const saved = fetch.mock.calls.find((call) => call[1]?.method === 'PUT');
    expect(JSON.parse(String(saved?.[1]?.body)).body).toBe('Changed wording');
  });

  it('refreshes changed wording after stale approval and sends its newly displayed version', async () => {
    let changed = false;
    let approved = false;
    const reviews: string[] = [];
    const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init?.method === 'POST') {
        const payload = JSON.parse(String(init.body));
        reviews.push(payload.review_sha256);
        if (!changed) {
          changed = true;
          return new Response(
            JSON.stringify({ detail: 'Template wording changed after it was displayed.' }),
            { status: 409 },
          );
        }
        expect(payload.review_sha256).toBe('b'.repeat(64));
        approved = true;
        return new Response(JSON.stringify({ approved: true }), { status: 200 });
      }
      return new Response(
        JSON.stringify([
          {
            id: 'template',
            name: 'TEST reply',
            body: changed ? 'New TEST wording to review.' : 'Old displayed TEST wording.',
            keywords: changed ? ['booking'] : ['rental'],
            approved,
            review_sha256: (changed ? 'b' : 'a').repeat(64),
          },
        ]),
        { status: 200 },
      );
    });
    vi.stubGlobal('fetch', fetcher);
    render(<EmailTemplates selectedIds={[]} onSelected={vi.fn()} />);
    await userEvent.click(screen.getByText('Reply templates', { exact: true }));
    await screen.findByText('Old displayed TEST wording.');
    await userEvent.click(screen.getByRole('button', { name: 'Approve this wording' }));
    await screen.findByText('New TEST wording to review.');
    expect(screen.getByText('Matching keywords: booking')).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent('changed');
    await userEvent.click(screen.getByRole('button', { name: 'Approve this wording' }));
    expect(await screen.findByRole('checkbox', { name: 'TEST reply · Approved' })).toBeEnabled();
    expect(reviews).toEqual(['a'.repeat(64), 'b'.repeat(64)]);
  });
});
