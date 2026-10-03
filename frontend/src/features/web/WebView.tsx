import { ExternalLink, MessageSquare, Search } from 'lucide-react';
import { useState } from 'react';
import { errorMessage, post, request } from '../../api/client';
import { EmptyState, ErrorNotice, Loading } from '../../components/ui';
import { useResource } from '../../hooks/useResource';
import type { Station } from '../../hooks/useStation';
import type { IntegrationStatus, WebSource } from '../../types';
import { SearxSetup } from './SearxSetup';

const loadStatus = () => request<IntegrationStatus>('/web/status');
export function WebView({ station }: { station: Station }) {
  const status = useResource(loadStatus);
  const [query, setQuery] = useState('');
  const [research, setResearch] = useState(false);
  const [sources, setSources] = useState<WebSource[]>([]);
  const [selected, setSelected] = useState<WebSource | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [error, setError] = useState('');
  async function search() {
    if (!query.trim()) return;
    setBusy(true);
    setError('');
    setSelected(null);
    try {
      if (research) {
        const result = await post<{ sources: WebSource[] }>('/web/research', {
          queries: query
            .split('\n')
            .map((value) => value.trim())
            .filter(Boolean)
            .slice(0, 4),
          limit: 6,
        });
        setSources(result.sources);
      } else {
        const result = await post<{ results: WebSource[] }>('/web/search', { query, limit: 10 });
        setSources(result.results);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function preview(source: WebSource) {
    setSelected(source);
    if (source.text) return;
    setPreviewBusy(true);
    setError('');
    try {
      const result = await post<{ text: string; title: string }>('/web/fetch', { url: source.url });
      setSelected({ ...source, text: result.text, title: result.title || source.title });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPreviewBusy(false);
    }
  }
  function sendToChat(items: WebSource[]) {
    station.beginChat(query || 'Summarize the selected sources.', [], items.slice(0, 4));
  }
  return (
    <div className="feature-page">
      <div className="page-heading">
        <div>
          <h1>Web</h1>
          <p>Search, read sources, and gather evidence.</p>
        </div>
        <button className="button secondary" onClick={() => void status.refresh()}>
          Check connection
        </button>
      </div>
      <ErrorNotice message={error || status.error} />
      {status.data && !status.data.available ? (
        <div className="integration-setup">
          <h3>Set up SearXNG</h3>
          <p>{status.data.message}</p>
          <SearxSetup />
          <button className="text-button" onClick={() => station.setPage('settings')}>
            Configure the search endpoint in Settings
          </button>
        </div>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
        className="web-search"
      >
        <label className="search-field">
          <Search size={19} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search the web"
            placeholder="Search the web"
          />
        </label>
        <button className="button" type="submit" disabled={busy || !query.trim()}>
          {research ? 'Research' : 'Search'}
        </button>
      </form>
      <label className="toggle-field research-toggle">
        <input
          type="checkbox"
          checked={research}
          onChange={(event) => setResearch(event.target.checked)}
        />
        Fetch sources for research
      </label>
      {busy ? (
        <Loading text={research ? 'Searching and reading sources…' : 'Searching…'} />
      ) : sources.length ? (
        <div className="web-workspace">
          <div className="source-list">
            <div className="source-list-header">
              <span className="subtle">{sources.length} sources</span>
              <button className="text-button" onClick={() => sendToChat(sources)}>
                <MessageSquare size={14} />
                Send to chat
              </button>
            </div>
            {sources.map((source) => (
              <button
                className={`source-row ${selected?.url === source.url ? 'selected' : ''}`}
                key={source.url}
                onClick={() => void preview(source)}
              >
                <span className="source-host">{new URL(source.url).hostname}</span>
                <h3>{source.title}</h3>
                <p>{source.snippet}</p>
              </button>
            ))}
          </div>
          <div className="source-preview">
            {selected ? (
              <>
                <div className="preview-header">
                  <a href={selected.url} target="_blank" rel="noreferrer">
                    Open source <ExternalLink size={13} />
                  </a>
                  <button className="text-button" onClick={() => sendToChat([selected])}>
                    Use in chat
                  </button>
                </div>
                <h2>{selected.title}</h2>
                {previewBusy ? (
                  <Loading text="Reading source…" />
                ) : (
                  <div className="article-text">
                    {selected.text ?? selected.fetch_error ?? selected.snippet}
                  </div>
                )}
              </>
            ) : (
              <p className="muted">Select a source to read it.</p>
            )}
          </div>
        </div>
      ) : (
        <EmptyState title="Search the web">
          Search results and source previews appear here.
        </EmptyState>
      )}
    </div>
  );
}
