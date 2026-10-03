import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { ApprovalCard } from '../src/features/google/ApprovalCard';

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
