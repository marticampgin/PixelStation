import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useStation } from '../src/hooks/useStation';

const json = (value: unknown) => new Response(JSON.stringify(value));
function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((success) => {
    resolve = success;
  });
  return { promise, resolve };
}
const conversation = {
  id: 'previous',
  title: 'Previous conversation',
  created_at: '2026-10-04',
  updated_at: '2026-10-04',
  archived: false,
  summary: '',
  messages: [],
};
function setup(creation = deferred(), detail = deferred()) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.startsWith('/api/conversations?')) return json([]);
    if (path === '/api/models') return json({ available: true, models: [] });
    if (path === '/api/settings') return json({ roles: { primary_chat: '' } });
    if (path === '/api/conversations' && init?.method === 'POST') return creation.promise;
    if (path === '/api/conversations/previous') return detail.promise;
    if (path.endsWith('/messages')) {
      if (init?.signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      return new Response('{"type":"token","content":"Old response"}\n');
    }
    if (path.endsWith('/cancel')) return json({ cancelled: true });
    throw new Error(`Unexpected request ${path}`);
  });
  vi.stubGlobal('fetch', fetcher);
  const hook = renderHook(() => useStation());
  return { ...hook, fetcher, creation, detail };
}

describe('station navigation request ownership', () => {
  it('keeps New chat blank after cancelling a conversation creation that finishes late', async () => {
    const { result, creation, detail, fetcher } = setup();
    await waitFor(() => expect(result.current.settings).not.toBeNull());
    act(() => result.current.setDraft('Previous request still creating a chat.'));
    let sending!: Promise<void>;
    act(() => {
      sending = result.current.send();
    });
    await waitFor(() =>
      expect(fetcher.mock.calls.some(([path]) => String(path) === '/api/conversations')).toBe(true),
    );
    act(() => result.current.setPage('gmail'));
    act(() => result.current.newChat());
    detail.resolve(json(conversation));
    await act(async () => {
      creation.resolve(json(conversation));
      await sending;
    });
    expect(result.current.page).toBe('chat');
    expect(result.current.conversationId).toBeNull();
    expect(result.current.conversation).toBeNull();
    expect(localStorage.getItem('pixel-station:v1:conversation')).toBe('null');
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/messages'))).toBe(false);
  });

  it('ignores a saved conversation detail that resolves after New chat from another page', async () => {
    localStorage.setItem('pixel-station:v1:conversation', JSON.stringify(conversation.id));
    const { result, detail } = setup();
    await waitFor(() => expect(result.current.loadingChat).toBe(true));
    act(() => result.current.setPage('gmail'));
    act(() => result.current.newChat());
    await act(async () => {
      detail.resolve(json(conversation));
      await detail.promise;
    });
    expect(result.current.conversationId).toBeNull();
    expect(result.current.conversation).toBeNull();
    expect(result.current.loadingChat).toBe(false);
  });
});
