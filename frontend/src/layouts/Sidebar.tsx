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
} from 'lucide-react';
import { useState } from 'react';
import { PixelMark } from '../components/ui';
import type { Station } from '../hooks/useStation';
import type { Page } from '../types';

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
  const chats = station.conversations.filter((chat) =>
    chat.title.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <aside className={`sidebar ${station.leftOpen ? '' : 'collapsed'}`}>
      <div className="brand">
        <PixelMark />
        {station.leftOpen ? <span>Pixel Station</span> : null}
        <button
          className="icon-button sidebar-toggle"
          onClick={() => station.setLeftOpen((value) => !value)}
          aria-label={station.leftOpen ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          {station.leftOpen ? <ChevronsLeft size={18} /> : <ChevronsRight size={18} />}
        </button>
      </div>
      <nav aria-label="Main navigation">
        {navigation.map(({ page, label, Icon }) => (
          <div key={page}>
            <button
              className={`nav-item ${station.page === page ? 'active' : ''}`}
              onClick={() => station.setPage(page)}
              title={!station.leftOpen ? label : undefined}
              aria-label={label}
            >
              <Icon size={22} strokeWidth={1.65} />
              {station.leftOpen ? <span>{label}</span> : null}
            </button>
            {page === 'chat' ? (
              <button className="new-chat button" onClick={station.newChat} title="New chat">
                <Plus size={20} />
                {station.leftOpen ? 'New chat' : null}
              </button>
            ) : null}
            {page === 'games' && station.page === 'games' && station.leftOpen ? (
              <div className="nav-subitem">Poker</div>
            ) : null}
          </div>
        ))}
      </nav>
      {station.leftOpen && station.page === 'chat' && station.hasHistory ? (
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
            <Search size={15} />
            <input
              aria-label="Search chats"
              placeholder="Search chats"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <div className="conversation-list">
            {chats.map((chat) => (
              <button
                key={chat.id}
                className={station.conversationId === chat.id ? 'selected' : ''}
                title={chat.title}
                onClick={() => station.openChat(chat.id)}
              >
                {chat.title}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
