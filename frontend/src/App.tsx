import { Box, PanelRightClose, PanelRightOpen } from 'lucide-react';
import { ErrorNotice, modelLabel } from './components/ui';
import { useStation } from './hooks/useStation';
import { Sidebar } from './layouts/Sidebar';
import { ContextPanel } from './layouts/ContextPanel';
import { ChatView } from './features/chat/ChatView';
import { FilesView } from './features/files/FilesView';
import { MemoryView } from './features/memory/MemoryView';
import { SettingsView } from './features/settings/SettingsView';
import { WebView } from './features/web/WebView';
import { ImageStudio } from './features/image-studio/ImageStudio';
import { GmailView } from './features/gmail/GmailView';
import { CalendarView } from './features/calendar/CalendarView';
import { PokerView } from './features/games/PokerView';

const titles = {
  chat: 'New chat',
  images: 'Image Studio',
  files: 'Files',
  memory: 'Memory',
  web: 'Web',
  gmail: 'Gmail',
  calendar: 'Calendar',
  games: 'Games',
  settings: 'Settings',
};
export function App() {
  const station = useStation();
  return (
    <div className="app-shell">
      <Sidebar station={station} />
      <main className="workspace">
        <header className="topbar">
          <h1>
            {station.page === 'chat'
              ? (station.conversation?.title ?? 'New chat')
              : titles[station.page]}
          </h1>
          <div className="topbar-controls">
            <label className="model-selector">
              <Box size={22} />
              <select
                aria-label="Active chat model"
                value={station.model}
                onChange={(event) => station.setModel(event.target.value)}
              >
                <option value="">Select model</option>
                {station.model &&
                !station.models.models.some((model) => model.name === station.model) ? (
                  <option value={station.model}>{modelLabel(station.model)} · missing</option>
                ) : null}
                {station.models.models
                  .filter(
                    (model) =>
                      !model.capabilities.includes('embedding') ||
                      model.capabilities.includes('completion'),
                  )
                  .map((model) => (
                    <option value={model.name} key={model.name}>
                      {modelLabel(model.name)}
                    </option>
                  ))}
              </select>
            </label>
            <span className="topbar-divider" />
            <button
              className="icon-button"
              aria-label={station.rightOpen ? 'Collapse context panel' : 'Expand context panel'}
              title={station.rightOpen ? 'Collapse context panel' : 'Expand context panel'}
              onClick={() => station.setRightOpen((value) => !value)}
            >
              {station.rightOpen ? <PanelRightClose size={22} /> : <PanelRightOpen size={22} />}
            </button>
          </div>
        </header>
        {station.error ? (
          <div className="global-error">
            <ErrorNotice message={station.error} dismiss={() => station.setError(null)} />
          </div>
        ) : null}
        {station.page === 'chat' ? <ChatView station={station} /> : null}
        {station.page === 'files' ? <FilesView station={station} /> : null}
        {station.page === 'memory' ? <MemoryView station={station} /> : null}
        {station.page === 'settings' ? <SettingsView station={station} /> : null}
        {station.page === 'web' ? <WebView station={station} /> : null}
        {station.page === 'images' ? <ImageStudio station={station} /> : null}
        {station.page === 'gmail' ? <GmailView /> : null}
        {station.page === 'calendar' ? <CalendarView /> : null}
        {station.page === 'games' ? <PokerView /> : null}
      </main>
      <ContextPanel station={station} />
    </div>
  );
}
