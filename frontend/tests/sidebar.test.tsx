import { useState } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../src/layouts/Sidebar';
import { Disclosure } from '../src/components/Disclosure';
import { useLocalStorage } from '../src/hooks/useLocalStorage';
import type { Station } from '../src/hooks/useStation';
import type { Conversation, Page } from '../src/types';

const chat: Conversation = {
  id: 'test-chat',
  title: 'Synthetic test conversation',
  archived: false,
  summary: '',
  created_at: '2026-10-03T10:00:00Z',
  updated_at: '2026-10-03T10:00:00Z',
};

function fixture() {
  const newChat = vi.fn();
  const setError = vi.fn();
  function Fixture() {
    const [page, setPage] = useState<Page>('memory');
    const [leftOpen, setLeftOpen] = useLocalStorage('left-open', true);
    const [showArchived, setShowArchived] = useState(false);
    const [conversations, setConversations] = useState([chat]);
    const station = {
      page,
      setPage,
      compact: false,
      leftOpen,
      setLeftOpen,
      conversations,
      conversationId: chat.id,
      showArchived,
      setShowArchived,
      newChat,
      openChat: () => setPage('chat'),
      busy: false,
      setError,
      refreshConversations: async () => setConversations([]),
    } as unknown as Station;
    return <Sidebar station={station} />;
  }
  return { Fixture, newChat, setError };
}

describe('sidebar groups and chat deletion', () => {
  it('persists independent groups without changing pages and exposes only the implemented game', async () => {
    const user = userEvent.setup();
    const { Fixture } = fixture();
    const mounted = render(<Fixture />);
    expect(screen.getByRole('button', { name: 'Memory' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('textbox', { name: 'Search chats' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Poker' })).toBeInTheDocument();
    expect(screen.queryByText('Chess')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Collapse chats' }));
    expect(screen.queryByRole('textbox', { name: 'Search chats' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Memory' })).toHaveAttribute('aria-current', 'page');
    expect(localStorage.getItem('pixel-station:v1:chats-group-open')).toBe('false');
    expect(screen.getByRole('button', { name: 'Poker' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Collapse games' }));
    expect(screen.queryByRole('button', { name: 'Poker' })).not.toBeInTheDocument();
    expect(localStorage.getItem('pixel-station:v1:games-group-open')).toBe('false');
    mounted.unmount();
    render(<Fixture />);
    expect(screen.getByRole('button', { name: 'Expand chats' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(screen.getByRole('button', { name: 'Expand games' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    await user.click(screen.getByRole('button', { name: 'Expand chats' }));
    await user.type(screen.getByRole('textbox', { name: 'Search chats' }), 'missing');
    expect(screen.getByText('No matching chats')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: chat.title })).not.toBeInTheDocument();
  });

  it('supports keyboard deletion, requires confirmation, and clears a deleted active chat', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ deleted: true })));
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    const { Fixture, newChat } = fixture();
    render(<Fixture />);
    screen.getByRole('button', { name: `Delete chat ${chat.title}` }).focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('dialog', { name: 'Delete chat?' })).toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: `Delete chat ${chat.title}` }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(fetcher).toHaveBeenCalledExactlyOnceWith(
      '/api/conversations/test-chat?confirmed=true',
      expect.objectContaining({ method: 'DELETE' }),
    );
    expect(newChat).toHaveBeenCalledOnce();
    expect(screen.queryByRole('button', { name: chat.title })).not.toBeInTheDocument();
  });

  it('keeps the conversation available after a failed delete', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ detail: 'Cancel the active response before deleting this chat' }),
            { status: 409 },
          ),
      ),
    );
    const user = userEvent.setup();
    const { Fixture, newChat, setError } = fixture();
    render(<Fixture />);
    await user.click(screen.getByRole('button', { name: `Delete chat ${chat.title}` }));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() =>
      expect(setError).toHaveBeenCalledWith('Cancel the active response before deleting this chat'),
    );
    expect(newChat).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: chat.title })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled();
  });

  it('removes collapsed disclosure controls from keyboard navigation and remembers expansion', async () => {
    const user = userEvent.setup();
    const content = (
      <Disclosure title="Attachments" preference="test-disclosure">
        <button>Download attachment</button>
      </Disclosure>
    );
    const mounted = render(content);
    await user.click(screen.getByRole('button', { name: 'Attachments' }));
    expect(screen.queryByRole('button', { name: 'Download attachment' })).not.toBeInTheDocument();
    expect(document.querySelector('.collapsible')).toHaveAttribute('inert');
    mounted.unmount();
    render(content);
    expect(screen.getByRole('button', { name: 'Attachments' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    await user.click(screen.getByRole('button', { name: 'Attachments' }));
    expect(screen.getByRole('button', { name: 'Download attachment' })).toBeInTheDocument();
  });
});
