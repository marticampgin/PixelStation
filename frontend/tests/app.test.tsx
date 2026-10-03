import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { ApprovalCard } from '../src/features/google/ApprovalCard';
import { HarnessReports } from '../src/features/settings/HarnessReports';
import { FileEditCard } from '../src/features/files/FileEditCard';

const model = 'hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q4_K_M';
const settings = {
  roles: { primary_chat: model, embedding: 'qwen3-embedding:0.6b' },
  ollama_url: 'http://127.0.0.1:11434',
  context_tokens: 8192,
  auto_memory: true,
  retrieval_count: 5,
  searxng_url: 'http://127.0.0.1:8888',
  comfyui_url: 'http://127.0.0.1:8188',
  critic_enabled: false,
  max_steps: 6,
  keep_alive: '5m',
  summary_turns: 8,
  profile: 'lite',
  harness_enabled: true,
  harness_interval_hours: 24,
};
const conversation = {
  id: 'chat-one',
  title: 'Persistent conversation',
  created_at: '2026-10-03T09:00:00Z',
  updated_at: '2026-10-03T09:00:00Z',
  archived: false,
  summary: '',
};
const json = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
function mockApi(custom?: (path: string, init?: RequestInit) => Response | undefined) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const extra = custom?.(path, init);
    if (extra) return extra;
    if (path.startsWith('/api/conversations?')) return json([conversation]);
    if (path === '/api/models')
      return json({ available: true, models: [{ name: model, capabilities: ['completion'] }] });
    if (path === '/api/settings') return json(settings);
    if (path === '/api/conversations/chat-one')
      return json({
        ...conversation,
        messages: [
          {
            id: 'existing',
            conversation_id: conversation.id,
            role: 'assistant',
            content: 'Saved message from an earlier session.',
            model,
            created_at: conversation.created_at,
            status: 'complete',
            attachment_ids: [],
            memory_ids: [],
            traces: [],
          },
        ],
      });
    if (
      path.startsWith('/api/memory?') ||
      path.startsWith('/api/files?') ||
      path.startsWith('/api/tools')
    )
      return json([]);
    if (path === '/api/integrations/approvals') return json({ approvals: [] });
    if (path === '/api/google/status')
      return json({
        configured: false,
        connected: false,
        message: 'Google is not configured.',
        scopes: [],
      });
    if (path.endsWith('/status'))
      return json({ available: true, endpoint: 'http://127.0.0.1', message: 'Available' });
    if (path === '/api/images/workflows')
      return json({
        workflows: [
          {
            id: 'wf-one',
            name: 'Test workflow',
            bindings: { prompt: { node: '6', input: 'text' } },
            created_at: conversation.created_at,
          },
        ],
        default_workflow: 'wf-one',
      });
    if (path === '/api/images/library') return json({ images: [] });
    return json({});
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

describe('workstation interactions', () => {
  it('starts compact views with visible chat, opens one drawer at a time, and preserves desktop preferences', async () => {
    let compact = true;
    const listeners = new Set<() => void>();
    vi.stubGlobal('matchMedia', () => ({
      get matches() {
        return compact;
      },
      addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    }));
    localStorage.setItem('pixel-station:v1:left-open', 'true');
    localStorage.setItem('pixel-station:v1:right-open', 'true');
    mockApi();
    const user = userEvent.setup();
    render(<App />);
    expect(
      await screen.findByRole('textbox', { name: 'Message Pixel Station' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('complementary', { name: 'Navigation panel' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: 'Context panel' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Expand context panel' }));
    expect(
      screen.queryByRole('complementary', { name: 'Navigation panel' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Context panel' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('complementary', { name: 'Context panel' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    await user.click(screen.getByRole('button', { name: 'Memory' }));
    expect(await screen.findByText('No memories yet')).toBeInTheDocument();
    expect(
      screen.queryByRole('complementary', { name: 'Navigation panel' }),
    ).not.toBeInTheDocument();
    expect(localStorage.getItem('pixel-station:v1:left-open')).toBe('true');
    expect(localStorage.getItem('pixel-station:v1:right-open')).toBe('true');

    act(() => {
      compact = false;
      listeners.forEach((listener) => listener());
    });
    expect(screen.getByRole('complementary', { name: 'Navigation panel' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Context panel' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    expect(localStorage.getItem('pixel-station:v1:left-open')).toBe('false');
    expect(localStorage.getItem('pixel-station:v1:right-open')).toBe('true');
  });

  it('persists independent panel states and navigates to real feature screens', async () => {
    mockApi();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('option', { name: 'LFM2.5 · 2.6B' });
    await user.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    expect(localStorage.getItem('pixel-station:v1:left-open')).toBe('false');
    await user.click(screen.getAllByRole('button', { name: 'Collapse context panel' })[0]);
    expect(screen.queryByRole('complementary', { name: 'Context panel' })).not.toBeInTheDocument();
    expect(localStorage.getItem('pixel-station:v1:right-open')).toBe('false');
    await user.click(screen.getByRole('button', { name: 'Memory' }));
    expect(await screen.findByText('No memories yet')).toBeInTheDocument();
    expect(localStorage.getItem('pixel-station:v1:left-open')).toBe('false');
  });

  it('reopens a persisted conversation from the server', async () => {
    localStorage.setItem('pixel-station:v1:conversation', JSON.stringify('chat-one'));
    mockApi();
    render(<App />);
    expect(await screen.findByText('Saved message from an earlier session.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Persistent conversation' })).toBeInTheDocument();
  });

  it('uploads a paperclip attachment and includes its stable ID in a streaming chat request', async () => {
    let sent: Record<string, unknown> | undefined;
    const fileRecord = {
      id: 'file-one',
      filename: 'notes.txt',
      size: 11,
      source: 'uploaded',
      extension: '.txt',
      created_at: conversation.created_at,
      media_type: 'text/plain',
      parse_status: 'parsed',
    };
    mockApi((path, init) => {
      if (path === '/api/files/upload') return json(fileRecord);
      if (path === '/api/conversations' && init?.method === 'POST') return json(conversation);
      if (path === '/api/conversations/chat-one/messages') {
        sent = JSON.parse(String(init?.body));
        return new Response('{"type":"token","content":"Response"}\n{"type":"done"}\n');
      }
      return undefined;
    });
    const user = userEvent.setup();
    const { container } = render(<App />);
    await screen.findByRole('option', { name: 'LFM2.5 · 2.6B' });
    const file = new File(['Hello world'], 'notes.txt', { type: 'text/plain' });
    await user.upload(container.querySelector('input[type=file]')!, file);
    expect(
      await screen.findByRole('button', { name: 'Remove attachment notes.txt' }),
    ).toBeInTheDocument();
    await user.type(
      screen.getByRole('textbox', { name: 'Message Pixel Station' }),
      'Read my notes',
    );
    await user.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(sent?.attachment_ids).toEqual(['file-one']));
    expect(sent?.content).toBe('Read my notes');
  });

  it('clears rejected streaming content on reset while preserving the user request and repaired tokens', async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(value) {
        controller = value;
      },
    });
    mockApi((path, init) => {
      if (path === '/api/conversations' && init?.method === 'POST') return json(conversation);
      if (path === '/api/conversations/chat-one/messages') return new Response(body);
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(
      screen.getByRole('textbox', { name: 'Message Pixel Station' }),
      'Plan the next step',
    );
    await user.click(screen.getByRole('button', { name: 'Send message' }));
    const enqueue = (event: Record<string, unknown>) =>
      act(() => {
        controller.enqueue(new TextEncoder().encode(`${JSON.stringify(event)}\n`));
      });
    enqueue({ type: 'token', content: 'Invalid partial response [read(path="notes.csv")]' });
    expect(
      await screen.findByText('Invalid partial response [read(path="notes.csv")]'),
    ).toBeInTheDocument();
    enqueue({ type: 'reset', detail: 'Retrying with a direct answer to your latest request.' });
    expect(
      await screen.findByText('Retrying with a direct answer to your latest request.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Invalid partial response/)).not.toBeInTheDocument();
    expect(screen.getByText('Plan the next step')).toBeInTheDocument();
    enqueue({ type: 'token', content: 'Here is the corrected plan.' });
    expect(await screen.findByText('Here is the corrected plan.')).toBeInTheDocument();
    expect(screen.queryByText(/Invalid partial response/)).not.toBeInTheDocument();
    await act(async () => controller.close());
  });

  it('submits Image Studio prompts directly to the image API with dimensions', async () => {
    let submitted: Record<string, unknown> | undefined;
    mockApi((path, init) => {
      if (path === '/api/images/generate') {
        submitted = JSON.parse(String(init?.body));
        return json({ id: 'job-one', status: 'queued', progress: 0, images: [] });
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Image Studio' }));
    await screen.findByRole('option', { name: /Test workflow/ });
    await user.type(screen.getByPlaceholderText('Describe the image'), 'Purple mountain');
    await user.click(screen.getByRole('button', { name: 'Generate image' }));
    await waitFor(() =>
      expect(submitted).toMatchObject({
        prompt: 'Purple mountain',
        workflow_id: 'wf-one',
        width: 512,
        height: 512,
      }),
    );
    expect(await screen.findByRole('button', { name: 'Cancel generation' })).toBeInTheDocument();
    expect(localStorage.getItem('pixel-station:v1:image-job')).toBe('"job-one"');
  });

  it('restores an existing image job when reopening the workspace and allows cancellation', async () => {
    localStorage.setItem('pixel-station:v1:image-job', '"previous-job"');
    const fetcher = mockApi((path, init) =>
      path === '/api/images/jobs/previous-job'
        ? json({
            id: 'previous-job',
            status: init?.method === 'DELETE' ? 'cancelled' : 'running',
            progress: 0.4,
            images: [],
          })
        : undefined,
    );
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Image Studio' }));
    expect(await screen.findByText('running · 40%')).toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalledWith('/api/images/generate', expect.anything());
    await user.click(screen.getByRole('button', { name: 'Cancel generation' }));
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        '/api/images/jobs/previous-job',
        expect.objectContaining({ method: 'DELETE' }),
      ),
    );
  });

  it('retries remote cancellation after failure and preserves the returned cleanup state', async () => {
    localStorage.setItem('pixel-station:v1:image-job', '"failed-job"');
    let retries = 0;
    mockApi((path, init) => {
      if (path !== '/api/images/jobs/failed-job') return undefined;
      if (init?.method === 'DELETE') retries += 1;
      return json({
        id: 'failed-job',
        status: retries > 1 ? 'cancelled' : 'failed',
        progress: 0,
        images: [],
        prompt_id: 'remote-prompt',
        remote_cleanup_required: retries < 2,
        error:
          retries === 1
            ? 'Remote cleanup failed again.'
            : retries === 0
              ? 'ComfyUI disconnected.'
              : undefined,
      });
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Image Studio' }));
    await user.click(await screen.findByRole('button', { name: 'Retry remote cancellation' }));
    expect(await screen.findByText('Remote cleanup failed again.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry remote cancellation' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Retry remote cancellation' }));
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Retry remote cancellation' }),
      ).not.toBeInTheDocument(),
    );
    expect(retries).toBe(2);
  });

  it('offers compatible role models, keeps missing selections visible, and applies the installed official Lite model', async () => {
    const alias = 'pixel-station-lfm2.5:2.6b';
    const official = 'LiquidAI/lfm2.5-2.6b:latest';
    mockApi((path) => {
      if (path === '/api/models')
        return json({
          available: true,
          models: [
            { name: model, capabilities: ['completion'] },
            { name: alias, capabilities: ['completion', 'thinking'] },
            { name: official, capabilities: ['completion', 'thinking'] },
            { name: 'qwen3-embedding:0.6b', capabilities: ['embedding'] },
            { name: 'vision-model', capabilities: ['vision', 'completion'] },
          ],
        });
      if (path === '/api/settings')
        return json({
          ...settings,
          roles: {
            ...settings.roles,
            planner: 'qwen3-embedding:0.6b',
            vision: 'missing-vision:2b',
          },
        });
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Settings' }));
    const embedding = await screen.findByRole('combobox', { name: /^embedding/ });
    expect(
      within(embedding)
        .getAllByRole('option')
        .map((option) => (option as HTMLOptionElement).value),
    ).toEqual(['', 'qwen3-embedding:0.6b']);
    const vision = screen.getByRole('combobox', { name: /^vision/ });
    expect(
      within(vision)
        .getAllByRole('option')
        .map((option) => (option as HTMLOptionElement).value),
    ).toEqual(['', 'missing-vision:2b', 'vision-model']);
    expect(screen.getByText('ollama pull missing-vision:2b')).toBeInTheDocument();
    expect(
      within(screen.getByRole('combobox', { name: /^planner/ })).getByRole('option', {
        name: /incompatible/,
      }),
    ).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /^Lite/ }));
    expect(screen.getByRole('combobox', { name: /^primary chat/ })).toHaveValue(official);
  });

  it('requests only the poker actions the deterministic engine exposes', async () => {
    let submitted: Record<string, unknown> | undefined;
    const state = {
      id: 'poker-one',
      hand_number: 1,
      stage: 'preflop',
      board: [],
      pot: 15,
      dealer: 0,
      actor: 0,
      seats: [
        {
          index: 0,
          name: 'You',
          stack: 995,
          bet: 5,
          contribution: 5,
          folded: false,
          all_in: false,
          hole: ['As', 'Kh'],
          personality: 'human',
        },
        {
          index: 1,
          name: 'AI 1',
          stack: 990,
          bet: 10,
          contribution: 10,
          folded: false,
          all_in: false,
          hole: [],
          personality: 'balanced',
        },
      ],
      legal_actions: [{ action: 'fold' }, { action: 'call', amount: 5 }],
      history: [],
      winners: [],
      completed: false,
    };
    mockApi((path, init) => {
      if (path === '/api/poker/sessions') return json(state);
      if (path === '/api/poker/sessions/poker-one/actions') {
        submitted = JSON.parse(String(init?.body));
        return json({ ...state, completed: true, legal_actions: [] });
      }
      if (path === '/api/poker/sessions/poker-one') return json(state);
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Games' }));
    await user.click(screen.getByRole('button', { name: /Poker No-Limit/ }));
    await user.click(screen.getByRole('button', { name: 'Start table' }));
    await user.click(await screen.findByRole('button', { name: 'Call 5' }));
    await waitFor(() => expect(submitted).toEqual({ action: 'call' }));
    expect(await screen.findByRole('button', { name: 'Next hand' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Raise' })).not.toBeInTheDocument();
  });

  it('keeps selected web source text separate from the task used for routing', async () => {
    let submitted: Record<string, unknown> | undefined;
    mockApi((path, init) => {
      if (path === '/api/web/search')
        return json({
          results: [
            {
              url: 'https://example.com/guide',
              title: 'Generate an image with local AI',
              snippet: 'Untrusted source text with action phrases.',
            },
          ],
        });
      if (path === '/api/conversations' && init?.method === 'POST') return json(conversation);
      if (path === '/api/conversations/chat-one/messages') {
        submitted = JSON.parse(String(init?.body));
        return new Response('{"type":"token","content":"Reply"}\n');
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Web' }));
    await user.type(screen.getByRole('textbox', { name: 'Search the web' }), 'Local model guide');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    await user.click(await screen.findByRole('button', { name: 'Send to chat' }));
    expect(screen.getByRole('textbox', { name: 'Message Pixel Station' })).toHaveValue(
      'Local model guide',
    );
    await user.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() =>
      expect(submitted?.web_sources).toEqual([
        { url: 'https://example.com/guide', title: 'Generate an image with local AI' },
      ]),
    );
    expect(submitted?.content).toBe('Local model guide');
  });

  it('preserves all-day calendar event boundaries when editing only the title', async () => {
    let submitted: { event: { summary: string; start: unknown; end: unknown } } | undefined;
    mockApi((path, init) => {
      if (path === '/api/google/status')
        return json({ configured: true, connected: true, message: 'Connected', scopes: [] });
      if (path === '/api/google/calendar/calendars') return json({ items: [] });
      if (path.startsWith('/api/google/calendar/events?'))
        return json({
          items: [
            {
              id: 'event-one',
              summary: 'All-day event',
              start: { date: '2026-10-09' },
              end: { date: '2026-10-10' },
            },
          ],
        });
      if (path === '/api/google/calendar/events/event-one' && init?.method === 'PATCH') {
        submitted = JSON.parse(String(init.body));
        return json({
          approval: {
            id: 'event-approval',
            action: 'calendar_update',
            payload: submitted,
            expires_at: conversation.created_at,
          },
        });
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Calendar' }));
    await user.click(await screen.findByRole('button', { name: 'Edit event' }));
    await user.clear(screen.getByRole('textbox', { name: 'Title' }));
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Updated event');
    await user.click(screen.getByRole('button', { name: 'Review changes' }));
    await waitFor(() =>
      expect(submitted?.event).toMatchObject({
        summary: 'Updated event',
        start: { date: '2026-10-09' },
        end: { date: '2026-10-10' },
      }),
    );
    expect(screen.getByRole('button', { name: 'Confirm action' })).toBeInTheDocument();
  });

  it('requires review and confirmation before replacing a file', async () => {
    const file = {
      id: 'file-one',
      filename: 'notes.txt',
      extension: '.txt',
      size: 8,
      source: 'uploaded',
      media_type: 'text/plain',
      created_at: conversation.created_at,
      parse_status: 'parsed',
    };
    const confirmed = vi.fn();
    mockApi((path, init) => {
      if (path.startsWith('/api/files?')) return json([file]);
      if (path === '/api/files/file-one/edit-content')
        return json({
          file_id: file.id,
          filename: file.filename,
          format: 'txt',
          content: 'Original',
          scope: 'Full text',
        });
      if (path === '/api/files/file-one/edit-proposals')
        return json({
          id: 'edit-one',
          file_id: file.id,
          filename: file.filename,
          preview_content: 'Revised',
          plan: 'Update content',
          scope: 'Full text',
        });
      if (path === '/api/files/edit-proposals/edit-one/confirm') {
        confirmed(init);
        return json(file);
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: 'Files' }));
    await user.click(await screen.findByRole('button', { name: 'Edit notes.txt' }));
    await user.clear(screen.getByRole('textbox', { name: 'Content' }));
    await user.type(screen.getByRole('textbox', { name: 'Content' }), 'Revised');
    await user.click(screen.getByRole('button', { name: 'Review file changes' }));
    await screen.findByRole('heading', { name: 'Replace notes.txt?' });
    expect(confirmed).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() =>
      expect(confirmed).toHaveBeenCalledWith(
        expect.objectContaining({ body: '{"confirmed":true}' }),
      ),
    );
  });
});

describe('confirmation enforcement', () => {
  it('confirms a chat file-edit proposal exactly once and exposes the preserved original revision', async () => {
    const fetcher = mockApi((path) =>
      path === '/api/files/edit-proposals/chat-edit/confirm'
        ? json({ id: 'file-one', revision_id: 'original-one' })
        : undefined,
    );
    const user = userEvent.setup();
    render(
      <FileEditCard
        proposal={{
          id: 'chat-edit',
          file_id: 'file-one',
          filename: 'notes.txt',
          plan: 'Update the reviewed paragraph.',
          preview_content: 'Reviewed replacement',
        }}
      />,
    );
    expect(fetcher).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Confirm file edit' }));
    await screen.findByText('File updated');
    expect(screen.getByRole('link', { name: 'Download original revision' })).toHaveAttribute(
      'href',
      '/api/files/file-one/revisions/original-one/content',
    );
    expect(screen.queryByRole('button', { name: 'Confirm file edit' })).not.toBeInTheDocument();
  });
  it('sends the stored approval ID only after explicit confirmation and prevents repeat clicks', async () => {
    const fetcher = mockApi();
    const user = userEvent.setup();
    render(
      <ApprovalCard
        approval={{
          id: 'approval-one',
          action: 'gmail_send',
          payload: { to: 'person@example.com', body: 'Reviewed email' },
          expires_at: conversation.created_at,
        }}
      />,
    );
    expect(fetcher).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Confirm action' }));
    await screen.findByText('Action completed');
    expect(fetcher).toHaveBeenCalledWith(
      '/api/integrations/approvals/approval-one/confirm',
      expect.objectContaining({ method: 'POST', body: '{"confirmed":true}' }),
    );
    expect(screen.queryByRole('button', { name: 'Confirm action' })).not.toBeInTheDocument();
  });
});

describe('harness reports', () => {
  it('presents actual findings and suggested fixes as readable report content', () => {
    render(
      <HarnessReports
        reports={[
          {
            id: 'report-one',
            created_at: conversation.created_at,
            report: {
              window_days: 7,
              event_count: 2,
              findings: [
                {
                  kind: 'response_error',
                  count: 2,
                  proposal: 'Verify the local model connection.',
                  examples: ['Ollama connection refused'],
                },
              ],
              regression_candidates: [],
            },
          },
        ]}
      />,
    );
    expect(screen.getByText('2 friction events · 7 days')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'response error' })).toBeInTheDocument();
    expect(screen.getByText('Verify the local model connection.')).toBeInTheDocument();
    expect(screen.getByText('Ollama connection refused')).toBeInTheDocument();
  });
});
