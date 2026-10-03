import {
  CalendarDays,
  ChevronsLeft,
  ChevronsRight,
  Database,
  File,
  Gamepad2,
  Globe2,
  Image,
  Mail,
  MessageSquare,
  Plus,
  Search,
  Settings,
  Spade,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';
import { core } from '../api/services';
import { errorMessage } from '../api/client';
import { Collapsible, DisclosureToggle } from '../components/Disclosure';
import { ConfirmDialog, PixelMark } from '../components/ui';
import { useLocalStorage } from '../hooks/useLocalStorage';
import type { Station } from '../hooks/useStation';
import type { Conversation, Page } from '../types';

const navigation = [
  { page: 'chat', label: 'Chats', Icon: MessageSquare },
  { page: 'images', label: 'Image Studio', Icon: Image },
  { page: 'files', label: 'Files', Icon: File },
  { page: 'memory', label: 'Memory', Icon: Database },
  { page: 'web', label: 'Web', Icon: Globe2 },
  { page: 'gmail', label: 'Gmail', Icon: Mail },
  { page: 'calendar', label: 'Calendar', Icon: CalendarDays },
  { page: 'games', label: 'Games', Icon: Gamepad2 },
  { page: 'settings', label: 'Settings', Icon: Settings },
] satisfies { page: Page; label: string; Icon: typeof MessageSquare }[];

export function Sidebar({ station }: { station: Station }) {
  const [search, setSearch] = useState('');
  const [chatsOpen, setChatsOpen] = useLocalStorage('chats-group-open', true);
  const [gamesOpen, setGamesOpen] = useLocalStorage('games-group-open', true);
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState(false);
  const chats = station.conversations.filter((chat) =>
    chat.title.toLowerCase().includes(search.toLowerCase()),
  );
  const hidden = station.compact && !station.leftOpen;

  async function deleteChat() {
    if (!pendingDelete || deleting) return;
    setDeleting(true);
    try {
      await core.deleteConversation(pendingDelete.id);
      if (station.conversationId === pendingDelete.id) station.newChat();
      setPendingDelete(null);
      await station.refreshConversations();
    } catch (error) {
      station.setError(errorMessage(error));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <aside
        id="navigation-panel"
        aria-label="Navigation panel"
        aria-hidden={hidden}
        inert={hidden}
        className={`sidebar ${!station.compact && !station.leftOpen ? 'collapsed' : ''} ${hidden ? 'drawer-closed' : ''}`}
      >
        <div className="rail-scene" aria-hidden="true" />
        <div className="brand">
          <PixelMark />
          <span className="brand-name">Pixel Station</span>
          <button
            className="icon-button panel-toggle sidebar-toggle"
            onClick={() => {
              station.setLeftOpen((value) => !value);
              if (station.compact) document.getElementById('navigation-panel-toggle')?.focus();
            }}
            aria-label={station.leftOpen ? 'Collapse sidebar' : 'Expand sidebar'}
            aria-expanded={station.leftOpen}
            aria-controls="navigation-panel"
          >
            {station.leftOpen ? <ChevronsLeft size={18} /> : <ChevronsRight size={18} />}
          </button>
        </div>
        <nav aria-label="Main navigation">
          {navigation.map(({ page, label, Icon }) => (
            <div key={page} className="nav-section">
              <div className={`nav-row ${station.page === page ? 'active' : ''}`}>
                <button
                  className="nav-item"
                  onClick={() => station.setPage(page)}
                  title={!station.leftOpen ? label : undefined}
                  aria-label={label}
                  aria-current={station.page === page ? 'page' : undefined}
                >
                  <Icon size={20} strokeWidth={1.65} />
                  <span className="nav-label">{label}</span>
                </button>
                {page === 'chat' && station.leftOpen ? (
                  <button
                    className="icon-button new-chat"
                    onClick={station.newChat}
                    title="New chat"
                    aria-label="New chat"
                  >
                    <Plus size={18} />
                  </button>
                ) : null}
                {(page === 'chat' || page === 'games') && station.leftOpen ? (
                  <DisclosureToggle
                    open={page === 'chat' ? chatsOpen : gamesOpen}
                    onClick={() =>
                      page === 'chat'
                        ? setChatsOpen((open) => !open)
                        : setGamesOpen((open) => !open)
                    }
                    controls={`${page}-group`}
                    label={label.toLowerCase()}
                  />
                ) : null}
              </div>
              {page === 'chat' ? (
                <Collapsible id="chat-group" open={station.leftOpen && chatsOpen}>
                  <div className="recent-chats">
                    <div className="history-heading">
                      <span className="rail-label">
                        {station.showArchived ? 'Archived chats' : 'Recent chats'}
                      </span>
                      <button
                        className="text-button"
                        onClick={() => station.setShowArchived((value) => !value)}
                      >
                        {station.showArchived ? 'Recent' : 'Archived'}
                      </button>
                    </div>
                    <label className="search-field small">
                      <Search size={14} />
                      <input
                        aria-label="Search chats"
                        placeholder="Search chats"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                      />
                    </label>
                    <div className="conversation-list">
                      {chats.map((chat) => (
                        <div
                          className={`conversation-row ${station.conversationId === chat.id ? 'selected' : ''}`}
                          key={chat.id}
                        >
                          <button
                            className="conversation-open"
                            title={chat.title}
                            onClick={() => station.openChat(chat.id)}
                          >
                            {chat.title}
                          </button>
                          <button
                            className="icon-button conversation-delete"
                            aria-label={`Delete chat ${chat.title}`}
                            title={`Delete ${chat.title}`}
                            onClick={() => setPendingDelete(chat)}
                            disabled={station.busy && station.conversationId === chat.id}
                          >
                            <Trash2 size={15} />
                          </button>
                        </div>
                      ))}
                      {!chats.length ? (
                        <p className="history-empty">
                          {search
                            ? 'No matching chats'
                            : station.showArchived
                              ? 'No archived chats'
                              : 'No chats yet'}
                        </p>
                      ) : null}
                    </div>
                  </div>
                </Collapsible>
              ) : null}
              {page === 'games' ? (
                <Collapsible id="games-group" open={station.leftOpen && gamesOpen}>
                  <button
                    className={`nav-game ${station.page === 'games' ? 'active' : ''}`}
                    onClick={() => station.setPage('games')}
                    aria-label="Poker"
                  >
                    <span className="nav-game-icon">
                      <Spade size={19} fill="currentColor" strokeWidth={1.2} />
                    </span>
                    <span>
                      <strong>Poker</strong>
                      <small>Texas Hold’em</small>
                    </span>
                  </button>
                </Collapsible>
              ) : null}
            </div>
          ))}
          {!station.leftOpen && !station.compact ? (
            <button
              className="icon-button collapsed-new-chat"
              onClick={station.newChat}
              title="New chat"
              aria-label="New chat"
            >
              <Plus size={20} />
            </button>
          ) : null}
        </nav>
      </aside>
      {pendingDelete ? (
        <ConfirmDialog
          title="Delete chat?"
          busy={deleting}
          onClose={() => {
            if (!deleting) setPendingDelete(null);
          }}
          confirm={() => void deleteChat()}
        >
          <p>Delete “{pendingDelete.title}” and its messages? This cannot be undone.</p>
          <p className="muted">Files and saved memories remain in your library.</p>
        </ConfirmDialog>
      ) : null}
    </>
  );
}
