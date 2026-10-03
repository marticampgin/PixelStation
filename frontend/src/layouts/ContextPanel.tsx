import { Box, ChevronsRight, Paperclip } from 'lucide-react';
import { useEffect, useState } from 'react';
import { core } from '../api/services';
import { Disclosure } from '../components/Disclosure';
import { modelLabel } from '../components/ui';
import { useLocalStorage } from '../hooks/useLocalStorage';
import type { Station } from '../hooks/useStation';
import type { LocalFile, Memory } from '../types';
import { ToolsContext } from './ToolsContext';

export function ContextPanel({ station }: { station: Station }) {
  const [tab, setTab] = useLocalStorage('context-tab', 'Context');
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
  return (
    <aside
      id="context-panel"
      className={`context-panel ${station.rightOpen ? '' : 'closed'}`}
      aria-label="Context panel"
      aria-hidden={!station.rightOpen}
      inert={!station.rightOpen}
    >
      <div className="rail-scene" aria-hidden="true" />
      <div className="context-inner">
        <div className="context-tabs">
          {['Context', 'Tools', 'Memory'].map((name) => (
            <button
              key={name}
              onClick={() => setTab(name)}
              className={name === tab ? 'active' : ''}
              aria-pressed={name === tab}
            >
              {name}
            </button>
          ))}
          <button
            className="icon-button panel-toggle"
            onClick={() => {
              station.setRightOpen(false);
              document.getElementById('context-panel-toggle')?.focus();
            }}
            aria-label="Collapse context panel"
          >
            <ChevronsRight size={18} />
          </button>
        </div>
        {tab === 'Context' ? (
          <div className="context-body">
            <Disclosure title="Active model" preference="context-model-open">
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
            </Disclosure>
            <Disclosure title="Attachments" preference="context-attachments-open">
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
            </Disclosure>
            {station.conversation?.summary ? (
              <Disclosure title="Conversation summary" preference="context-summary-open">
                <p className="context-summary">{station.conversation.summary}</p>
              </Disclosure>
            ) : null}
          </div>
        ) : null}
        {tab === 'Tools' ? (
          <div className="context-body">
            <ToolsContext message={last} busy={station.busy} />
            <Disclosure title="Tool activity" preference="context-activity-open">
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
            </Disclosure>
          </div>
        ) : null}
        {tab === 'Memory' ? (
          <div className="context-body">
            <Disclosure title="Retrieved memories" preference="context-memories-open">
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
            </Disclosure>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
