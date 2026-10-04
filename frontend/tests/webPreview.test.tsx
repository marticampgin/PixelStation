import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { WebView } from '../src/features/web/WebView';
import type { Station } from '../src/hooks/useStation';
import type { WebSource } from '../src/types';

const sources: WebSource[] = [
  { url: 'https://example.com/first', title: 'First source', snippet: 'First result summary.' },
  { url: 'https://example.org/second', title: 'Second source', snippet: 'Second result summary.' },
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
function setup(results = sources) {
  const pending = new Map(sources.map((source) => [source.url, deferred()]));
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === '/api/web/status')
      return json({ available: true, endpoint: 'http://127.0.0.1:8888', message: 'Available' });
    if (path === '/api/web/search') return json({ results });
    if (path === '/api/web/research') return json({ sources: results });
    if (path === '/api/web/fetch') {
      const { url } = JSON.parse(String(init?.body)) as { url: string };
      return pending.get(url)!.promise;
    }
    throw new Error(`Unexpected API path: ${path}`);
  });
  vi.stubGlobal('fetch', fetcher);
  const station = { beginChat: vi.fn(), setPage: vi.fn() };
  const view = render(<WebView station={station as unknown as Station} />);
  return { ...view, user: userEvent.setup(), pending, fetcher, station };
}
async function search(user: ReturnType<typeof userEvent.setup>, research = false) {
  await user.type(screen.getByRole('textbox', { name: 'Search the web' }), 'Local sources');
  if (research)
    await user.click(screen.getByRole('checkbox', { name: 'Fetch sources for research' }));
  await user.click(screen.getByRole('button', { name: research ? 'Research' : 'Search' }));
  await screen.findByRole('button', { name: /First source/ });
}

describe('Web source previews', () => {
  it('ignores a late preview without replacing the newer selection or clearing its loading state', async () => {
    const { user, pending, fetcher, station } = setup();
    await search(user);
    await user.click(screen.getByRole('button', { name: /First source/ }));
    await user.click(screen.getByRole('button', { name: /Second source/ }));
    const firstRequest = fetcher.mock.calls.find(
      ([path, init]) =>
        String(path) === '/api/web/fetch' && JSON.parse(String(init?.body)).url === sources[0].url,
    );
    expect(firstRequest?.[1]?.signal?.aborted).toBe(true);
    await act(async () => {
      pending
        .get(sources[0].url)!
        .resolve(json({ text: 'Obsolete first article.', title: 'First article' }));
    });
    expect(screen.getByRole('heading', { level: 2, name: 'Second source' })).toBeVisible();
    expect(screen.getByText('Reading source…')).toBeVisible();
    expect(screen.queryByText('Obsolete first article.')).not.toBeInTheDocument();
    await act(async () => {
      pending
        .get(sources[1].url)!
        .resolve(json({ text: 'Current second article.', title: 'Second article' }));
    });
    expect(screen.getByText('Current second article.')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Use in chat' }));
    expect(station.beginChat).toHaveBeenCalledWith(
      'Local sources',
      [],
      [{ ...sources[1], text: 'Current second article.', title: 'Second article' }],
    );
  });

  it('does not surface an obsolete fetch failure after the next source has loaded', async () => {
    const { user, pending } = setup();
    await search(user);
    await user.click(screen.getByRole('button', { name: /First source/ }));
    await user.click(screen.getByRole('button', { name: /Second source/ }));
    await act(async () => {
      pending
        .get(sources[1].url)!
        .resolve(json({ text: 'Current article.', title: 'Second source' }));
    });
    await act(async () =>
      pending.get(sources[0].url)!.reject(new Error('Obsolete network failure.')),
    );
    expect(screen.getByText('Current article.')).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('clears the previous loading state when selecting a source with retained text', async () => {
    const { user, pending, fetcher } = setup([
      sources[0],
      { ...sources[1], text: 'Retained article.' },
    ]);
    await search(user);
    await user.click(screen.getByRole('button', { name: /First source/ }));
    await user.click(screen.getByRole('button', { name: /Second source/ }));
    expect(screen.getByText('Retained article.')).toBeVisible();
    expect(screen.queryByText('Reading source…')).not.toBeInTheDocument();
    await act(async () => {
      pending
        .get(sources[0].url)!
        .resolve(json({ text: 'Obsolete article.', title: 'First source' }));
    });
    expect(screen.getByText('Retained article.')).toBeVisible();
    expect(fetcher.mock.calls.filter(([path]) => String(path) === '/api/web/fetch')).toHaveLength(
      1,
    );
  });

  it('invalidates a pending preview when starting a new search', async () => {
    const { user, pending } = setup();
    await search(user);
    await user.click(screen.getByRole('button', { name: /First source/ }));
    await user.click(screen.getByRole('button', { name: 'Search' }));
    await screen.findByRole('button', { name: /First source/ });
    await act(async () => {
      pending
        .get(sources[0].url)!
        .resolve(json({ text: 'Obsolete article.', title: 'First source' }));
    });
    expect(screen.getByText('Select a source to read it.')).toBeVisible();
    expect(screen.queryByRole('heading', { level: 2 })).not.toBeInTheDocument();
  });

  it('preserves a research fetch error and snippet when retained text is empty', async () => {
    const failed = { ...sources[0], text: '', fetch_error: 'Source access was blocked.' };
    const { user, fetcher } = setup([failed]);
    await search(user, true);
    await user.click(screen.getByRole('button', { name: /First source/ }));
    expect(screen.getByRole('alert')).toHaveTextContent(failed.fetch_error);
    expect(
      screen.getByRole('heading', { level: 2, name: failed.title }).parentElement,
    ).toHaveTextContent(failed.snippet);
    expect(fetcher.mock.calls.filter(([path]) => String(path) === '/api/web/fetch')).toHaveLength(
      0,
    );
  });

  it('falls back to the snippet when retained text is empty without a fetch error', async () => {
    const source = { ...sources[0], text: '' };
    const { user, fetcher } = setup([source]);
    await search(user, true);
    await user.click(screen.getByRole('button', { name: /First source/ }));
    expect(
      screen.getByRole('heading', { level: 2, name: source.title }).parentElement,
    ).toHaveTextContent(source.snippet);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([path]) => String(path) === '/api/web/fetch')).toHaveLength(
      0,
    );
  });
});

describe('Web empty results', () => {
  it.each([false, true])(
    'shows a completed empty result and clears it during the next request (research=%s)',
    async (research) => {
      const { user, fetcher } = setup([]);
      expect(screen.getByRole('heading', { name: 'Search the web' })).toBeVisible();
      const input = screen.getByRole('textbox', { name: 'Search the web' });
      await user.type(input, 'Rare local topic');
      if (research)
        await user.click(screen.getByRole('checkbox', { name: 'Fetch sources for research' }));
      const button = screen.getByRole('button', { name: research ? 'Research' : 'Search' });
      await user.click(button);
      expect(await screen.findByRole('heading', { name: 'No results found' })).toBeVisible();
      expect(screen.getByText(/No sources matched “Rare local topic”/)).toBeVisible();
      expect(screen.getByText(/enabled SearXNG engines/)).toBeVisible();
      expect(screen.queryByRole('heading', { name: 'Search the web' })).not.toBeInTheDocument();
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();

      await user.clear(input);
      await user.type(input, 'Broader topic');
      expect(screen.getByText(/No sources matched “Rare local topic”/)).toBeVisible();
      const next = deferred();
      fetcher.mockImplementationOnce(() => next.promise);
      await user.click(button);
      expect(screen.queryByRole('heading', { name: 'No results found' })).not.toBeInTheDocument();
      expect(
        screen.getByText(research ? 'Searching and reading sources…' : 'Searching…'),
      ).toBeVisible();
      await act(async () => next.resolve(json(research ? { sources: [] } : { results: [] })));
      expect(screen.getByText(/No sources matched “Broader topic”/)).toBeVisible();
    },
  );

  it.each([false, true])(
    'keeps a failed request distinct from a successful empty result (research=%s)',
    async (research) => {
      const { user, fetcher } = setup([]);
      await user.type(screen.getByRole('textbox', { name: 'Search the web' }), 'Local topic');
      if (research)
        await user.click(screen.getByRole('checkbox', { name: 'Fetch sources for research' }));
      fetcher.mockRejectedValueOnce(new Error('Search service could not be reached.'));
      await user.click(screen.getByRole('button', { name: research ? 'Research' : 'Search' }));
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Search service could not be reached.',
      );
      expect(screen.queryByRole('heading', { name: 'No results found' })).not.toBeInTheDocument();
    },
  );
});
