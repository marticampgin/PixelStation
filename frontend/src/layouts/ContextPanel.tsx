import { Box, ChevronsRight, Paperclip } from 'lucide-react';
import { useEffect, useState } from 'react';
import { core } from '../api/services';
import { modelLabel } from '../components/ui';
import type { Station } from '../hooks/useStation';
import type { LocalFile, Memory } from '../types';
import { ToolsContext } from './ToolsContext';

export function ContextPanel({ station }: { station: Station }) {
  const [tab, setTab] = useState('Context');
  const [files, setFiles] = useState<LocalFile[]>([]);
  const [historicMemories, setHistoricMemories] = useState<Memory[]>([]);
  const ids = station.conversation?.messages.flatMap((message) => message.attachment_ids) ?? [];
  const key = [...new Set(ids)].sort().join(',');
  useEffect(() => {
    if (key)
      core
        .files()
        .then((items) => setFiles(items.filter((item) => key.split(',').includes(item.id))))
        .catch(() => setFiles([]));
    else setFiles([]);
  }, [key]);

  const attached = [
    ...files,
    ...station.attachments.filter((file) => !files.some((existing) => existing.id === file.id)),
  ];
  const last = station.conversation?.messages
    .filter((message) => message.role === 'assistant')
    .at(-1);
  const memoryKey = last?.memory_ids.join(',') ?? '';
  useEffect(() => {
    if (memoryKey)
      void core
        .memories()
        .then((items) =>
          setHistoricMemories(items.filter((item) => memoryKey.split(',').includes(item.id))),
        )
        .catch(() => setHistoricMemories([]));
    else setHistoricMemories([]);
  }, [memoryKey]);
  const memories = station.retrievedMemories.length ? station.retrievedMemories : historicMemories;
  if (!station.rightOpen) return null;
  return (
    <aside id="context-panel" className="context-panel" aria-label="Context panel">
      <div className="context-tabs">
        {['Context', 'Tools', 'Memory'].map((name) => (
          <button key={name} onClick={() => setTab(name)} className={name === tab ? 'active' : ''}>
            {name}
          </button>
        ))}
        <button
          className="icon-button"
          onClick={() => station.setRightOpen(false)}
          aria-label="Collapse context panel"
        >
          <ChevronsRight size={18} />
        </button>
      </div>
      {tab === 'Context' ? (
        <div className="context-body">
          <section>
            <h3>Model</h3>
            <div className="model-context">
              <Box size={29} className="accent" />
              <div>
                <strong>{station.model ? modelLabel(station.model) : 'Select a model'}</strong>
                <div className="model-budget">
                  <span>Context window</span>
                  <span>{station.settings?.context_tokens.toLocaleString() ?? '—'} tokens</span>
                </div>
              </div>
            </div>
          </section>
          <section>
            <h3>Attachments</h3>
            {attached.length ? (
              attached.map((file) => (
                <a
                  href={`/api/files/${file.id}/content`}
                  target="_blank"
                  rel="noreferrer"
                  className="context-file"
                  key={file.id}
                >
                  <Paperclip size={18} />
                  <span>{file.filename}</span>
                </a>
              ))
            ) : (
              <div className="context-file muted">
                <Paperclip size={21} />
                <span>No attachments</span>
              </div>
            )}
          </section>
          {station.conversation?.summary ? (
            <section>
              <h3>Conversation summary</h3>
              <p className="context-summary">{station.conversation.summary}</p>
            </section>
          ) : null}
        </div>
      ) : null}
      {tab === 'Tools' ? (
        <div className="context-body">
          <ToolsContext message={last} busy={station.busy} />
          <section>
            <h3>Tool activity</h3>
            {station.status ? <p className="muted">{station.status}</p> : null}
            {last?.traces?.length ? (
              last.traces.map((trace, i) => (
                <pre className="trace" key={i}>
                  {JSON.stringify(trace, null, 2)}
                </pre>
              ))
            ) : (
              <p className="muted">Tool calls appear here when used.</p>
            )}
          </section>
        </div>
      ) : null}
      {tab === 'Memory' ? (
        <div className="context-body">
          <section>
            <h3>Retrieved memories</h3>
            {memories.length ? (
              memories.map((memory) => (
                <div className="context-memory" key={memory.id}>
                  <p>{memory.text}</p>
                  <span className="retrieval-reason">
                    {memory.retrieval_reason || 'Included in this response'}
                  </span>
                  <small className="muted">
                    {memory.category}
                    {memory.source_conversation_id ? (
                      <button
                        className="text-button"
                        onClick={() => station.openChat(memory.source_conversation_id!)}
                      >
                        Open source
                      </button>
                    ) : null}
                  </small>
                </div>
              ))
            ) : (
              <p className="muted">No memories retrieved for this response.</p>
            )}
          </section>
        </div>
      ) : null}
    </aside>
  );
}
