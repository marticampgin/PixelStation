import { ExternalLink, Pencil, Pin, Plus, Search, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { core } from '../../api/services';
import { errorMessage } from '../../api/client';
import {
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Loading,
  Modal,
  formatDate,
} from '../../components/ui';
import { useResource } from '../../hooks/useResource';
import type { Station } from '../../hooks/useStation';
import type { Memory, MemoryDetail } from '../../types';

const loadMemories = () => core.memories();
const categories = [
  'preference',
  'fact',
  'event',
  'project',
  'workflow',
  'person/entity',
  'instruction',
  'learned_pattern',
];
export function MemoryView({ station }: { station: Station }) {
  const resource = useResource(loadMemories);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [editing, setEditing] = useState<Partial<Memory> | null>(null);
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [deleting, setDeleting] = useState<Memory | null>(null);
  const [busy, setBusy] = useState(false);
  const memories =
    resource.data?.filter(
      (memory) =>
        (!category || memory.category === category) &&
        `${memory.text} ${memory.tags.join(' ')}`.toLowerCase().includes(query.toLowerCase()),
    ) ?? [];
  async function mutate(action: () => Promise<unknown>) {
    setBusy(true);
    resource.setError(null);
    try {
      await action();
      await resource.refresh();
      setEditing(null);
      setDeleting(null);
    } catch (err) {
      resource.setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function toggleAutomatic() {
    if (!station.settings) return;
    await mutate(async () => {
      const next = await core.saveSettings({
        ...station.settings!,
        auto_memory: !station.settings!.auto_memory,
      });
      station.setSettings(next);
    });
  }
  async function inspect(id: string) {
    try {
      setDetail(await core.memory(id));
    } catch (err) {
      resource.setError(errorMessage(err));
    }
  }
  return (
    <div className="feature-page">
      <div className="page-heading">
        <div>
          <h1>Memory</h1>
          <p>Facts, preferences, and decisions with their sources.</p>
        </div>
        <button
          className="button"
          onClick={() =>
            setEditing({ text: '', category: 'fact', importance: 0.5, tags: [], pinned: false })
          }
        >
          <Plus size={16} />
          Add memory
        </button>
      </div>
      <ErrorNotice message={resource.error} />
      <div className="toolbar">
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Search memories"
            placeholder="Search memory"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select
          aria-label="Memory category filter"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          <option value="">All categories</option>
          {categories.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </div>
      <label className="toggle-field memory-auto">
        <input
          type="checkbox"
          checked={station.settings?.auto_memory ?? false}
          disabled={!station.settings || busy}
          onChange={() => void toggleAutomatic()}
        />
        Automatic memory extraction
      </label>
      {resource.loading ? (
        <Loading />
      ) : memories.length ? (
        <div className="memory-list">
          {memories.map((memory) => (
            <article className="memory-row" key={memory.id}>
              <div className="memory-meta">
                <span>{memory.category}</span>
                <span>{formatDate(memory.updated_at)}</span>
                {memory.pinned ? <Pin size={13} className="accent" /> : null}
              </div>
              <p>{memory.text}</p>
              <div className="memory-footer">
                <span className="subtle">
                  Importance {Math.round(memory.importance * 100)}% · Confidence{' '}
                  {Math.round(memory.confidence * 100)}%
                </span>
                <div className="row-actions">
                  <button className="text-button" onClick={() => void inspect(memory.id)}>
                    Inspect
                  </button>
                  {memory.source_conversation_id ? (
                    <button
                      className="icon-button"
                      title="Open source conversation"
                      aria-label="Open source conversation"
                      onClick={() => station.openChat(memory.source_conversation_id!)}
                    >
                      <ExternalLink size={16} />
                    </button>
                  ) : null}
                  <button
                    className="icon-button"
                    title={memory.pinned ? 'Unpin memory' : 'Pin memory'}
                    aria-label={memory.pinned ? 'Unpin memory' : 'Pin memory'}
                    onClick={() =>
                      void mutate(() => core.updateMemory(memory.id, { pinned: !memory.pinned }))
                    }
                  >
                    <Pin size={16} />
                  </button>
                  <button
                    className="icon-button"
                    title="Edit memory"
                    aria-label="Edit memory"
                    onClick={() => setEditing(memory)}
                  >
                    <Pencil size={16} />
                  </button>
                  <button
                    className="icon-button"
                    title="Delete memory"
                    aria-label="Delete memory"
                    onClick={() => setDeleting(memory)}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState title={query || category ? 'No matching memories' : 'No memories yet'}>
          Add a memory here or ask Pixel Station to remember something.
        </EmptyState>
      )}
      {editing ? (
        <Modal title={editing.id ? 'Edit memory' : 'Add memory'} onClose={() => setEditing(null)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(() =>
                editing.id
                  ? core.updateMemory(editing.id, {
                      text: editing.text,
                      category: editing.category,
                      importance: editing.importance,
                      tags: editing.tags,
                    })
                  : core.createMemory(editing),
              );
            }}
          >
            <label>
              Memory
              <textarea
                required
                value={editing.text}
                onChange={(event) => setEditing({ ...editing, text: event.target.value })}
              />
            </label>
            <div className="form-grid">
              <label>
                Category
                <select
                  value={editing.category}
                  onChange={(event) => setEditing({ ...editing, category: event.target.value })}
                >
                  {categories.map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
              <label>
                Importance
                <input
                  type="number"
                  min="0"
                  max="1"
                  step=".1"
                  value={editing.importance}
                  onChange={(event) =>
                    setEditing({ ...editing, importance: Number(event.target.value) })
                  }
                />
              </label>
            </div>
            <label>
              Tags
              <input
                value={editing.tags?.join(', ') ?? ''}
                placeholder="Comma-separated tags"
                onChange={(event) =>
                  setEditing({
                    ...editing,
                    tags: event.target.value
                      .split(',')
                      .map((tag) => tag.trim())
                      .filter(Boolean),
                  })
                }
              />
            </label>
            <ErrorNotice message={resource.error} />
            <div className="modal-actions">
              <button className="button" disabled={busy} type="submit">
                Save memory
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
      {deleting ? (
        <ConfirmDialog
          title="Delete memory?"
          busy={busy}
          onClose={() => setDeleting(null)}
          confirm={() => void mutate(() => core.deleteMemory(deleting.id))}
        >
          {deleting.text}
        </ConfirmDialog>
      ) : null}
      {detail ? (
        <Modal title="Memory provenance" onClose={() => setDetail(null)}>
          <p>{detail.text}</p>
          <dl className="provenance">
            <dt>Category</dt>
            <dd>{detail.category}</dd>
            <dt>Scope</dt>
            <dd>{detail.scope}</dd>
            <dt>Created</dt>
            <dd>{formatDate(detail.created_at)}</dd>
            <dt>Source</dt>
            <dd>
              {detail.source_conversation_id ? (
                <button
                  className="text-button"
                  onClick={() => {
                    station.openChat(detail.source_conversation_id!);
                    setDetail(null);
                  }}
                >
                  Open conversation
                </button>
              ) : (
                'Added manually'
              )}
            </dd>
          </dl>
          <h3>Revisions</h3>
          {detail.revisions.length ? (
            <pre className="code-block">{JSON.stringify(detail.revisions, null, 2)}</pre>
          ) : (
            <p className="muted">No revisions.</p>
          )}
          <h3>Related memories</h3>
          {detail.links.length ? (
            <pre className="code-block">{JSON.stringify(detail.links, null, 2)}</pre>
          ) : (
            <p className="muted">No relationships yet.</p>
          )}
        </Modal>
      ) : null}
    </div>
  );
}
