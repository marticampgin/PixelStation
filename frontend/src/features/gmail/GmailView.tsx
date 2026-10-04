import { Download, FolderDown, Mail, MessageSquare, RefreshCw, Search, Send } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { errorMessage, post, request } from '../../api/client';
import { EmptyState, ErrorNotice, Loading, formatBytes } from '../../components/ui';
import { useResource } from '../../hooks/useResource';
import type {
  Approval,
  EmailMessage,
  EmailThread,
  GoogleServiceStatus,
  LocalFile,
} from '../../types';
import { ApprovalCard } from '../google/ApprovalCard';
import { GoogleSetup } from '../google/GoogleSetup';
import { replyTarget } from './replyTarget';
import { EmailTemplates } from './EmailTemplates';

const loadStatus = () => request<GoogleServiceStatus>('/google/gmail/status');
export function GmailView({ onAnalyze }: { onAnalyze?: (file: LocalFile) => void } = {}) {
  const status = useResource(loadStatus);
  const [threads, setThreads] = useState<EmailThread[]>([]);
  const [messages, setMessages] = useState<EmailMessage[]>([]);
  const [thread, setThread] = useState<EmailThread | null>(null);
  const [query, setQuery] = useState('');
  const [to, setTo] = useState('');
  const [inReplyTo, setInReplyTo] = useState<string | undefined>();
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [instructions, setInstructions] = useState('');
  const [busy, setBusy] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [approval, setApproval] = useState<Approval | null>(null);
  const [imported, setImported] = useState<Record<string, LocalFile>>({});
  const [availableFiles, setAvailableFiles] = useState<LocalFile[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<LocalFile[]>([]);
  const [showFiles, setShowFiles] = useState(false);
  const [templateIds, setTemplateIds] = useState<string[]>([]);
  const detailRequest = useRef<AbortController | null>(null);
  const working = busy || detailBusy;
  useEffect(
    () => () => {
      detailRequest.current?.abort();
      detailRequest.current = null;
    },
    [],
  );
  async function action(run: () => Promise<unknown>, success = '') {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await run();
      if (success) setNotice(success);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function loadThreads() {
    await action(async () => {
      const result = await request<{ threads: EmailThread[] }>(
        `/google/gmail/threads?q=${encodeURIComponent(query)}`,
      );
      setThreads(result.threads);
    });
  }
  useEffect(() => {
    if (status.data?.connected) void loadThreads();
  }, [status.data?.connected]);
  async function selectThread(item: EmailThread) {
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    setThread(item);
    setMessages([]);
    setTo('');
    setInReplyTo(undefined);
    setSubject('');
    setBody('');
    setInstructions('');
    setApproval(null);
    setSelectedFiles([]);
    setShowFiles(false);
    setTemplateIds([]);
    setDetailBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await request<{ messages: EmailMessage[] }>(
        `/google/gmail/threads/${item.id}`,
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setMessages(result.messages);
      const target = replyTarget(result.messages);
      setTo(target.recipient);
      setInReplyTo(target.messageId);
      setSubject(/^re:/i.test(item.subject) ? item.subject : `Re: ${item.subject}`);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err));
    } finally {
      if (detailRequest.current === controller) {
        detailRequest.current = null;
        setDetailBusy(false);
      }
    }
  }
  const payload = () => ({
    to,
    subject,
    body,
    thread_id: thread?.id,
    in_reply_to: inReplyTo,
    ...(selectedFiles.length ? { attachment_ids: selectedFiles.map((file) => file.id) } : {}),
  });
  const attachmentPath = (messageId: string, partId: string) =>
    `/google/gmail/messages/${encodeURIComponent(messageId)}/attachments/${encodeURIComponent(partId)}`;
  async function importAttachment(messageId: string, partId: string, analyze = false) {
    await action(async () => {
      const key = `${messageId}/${partId}`;
      const file =
        imported[key] ?? (await post<LocalFile>(`${attachmentPath(messageId, partId)}/import`));
      setImported((previous) => ({ ...previous, [key]: file }));
      setNotice(
        `Saved ${file.filename} to Files${file.parse_status === 'ready' ? '' : ` (${file.parse_status})`}.`,
      );
      if (analyze) onAnalyze?.(file);
    });
  }
  if (status.loading)
    return (
      <div className="feature-page">
        <Loading text="Checking Gmail connection…" />
      </div>
    );
  if (!status.data?.connected)
    return (
      <div className="feature-page">
        <div className="page-heading">
          <h1>Gmail</h1>
        </div>
        <ErrorNotice message={status.error} />
        <GoogleSetup service="gmail" onConnected={status.setData} />
      </div>
    );
  return (
    <div className="feature-page gmail-page">
      <div className="page-heading">
        <div>
          <h1>Gmail</h1>
          {status.data.account ? (
            <p>{status.data.account.email || status.data.account.label}</p>
          ) : null}
        </div>
        <button className="button secondary" disabled={working} onClick={() => void loadThreads()}>
          <RefreshCw size={15} />
          Refresh
        </button>
      </div>
      <ErrorNotice message={error} />
      {notice ? (
        <div className="notice" role="status">
          {notice}
        </div>
      ) : null}
      <form
        className="web-search"
        onSubmit={(event) => {
          event.preventDefault();
          void loadThreads();
        }}
      >
        <label className="search-field">
          <Search size={17} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search Gmail"
            aria-label="Search Gmail"
          />
        </label>
        <button className="button secondary" disabled={working} type="submit">
          Search
        </button>
      </form>
      <div className="mail-workspace">
        <div className="mail-list">
          {threads.map((item) => (
            <button
              key={item.id}
              className={`mail-thread ${item.id === thread?.id ? 'selected' : ''}`}
              disabled={busy}
              onClick={() => void selectThread(item)}
            >
              <strong>{item.subject || '(No subject)'}</strong>
              <span>{item.from}</span>
              <p>{item.snippet}</p>
              <time className="subtle">{item.date}</time>
            </button>
          ))}
          {!threads.length ? <p className="muted">No matching threads.</p> : null}
        </div>
        <div className="mail-detail">
          {thread ? (
            <>
              <h2>{thread.subject}</h2>
              {messages.map((message) => (
                <article key={message.id} className="email-message">
                  <div className="email-meta">
                    <strong>{message.from}</strong>
                    <time>{message.date}</time>
                  </div>
                  <div className="email-body">{message.body}</div>
                  {message.attachments.length ? (
                    <aside aria-label="Email attachments">
                      {message.attachments.map((attachment, index) => (
                        <div className="row-actions" key={attachment.part_id ?? index}>
                          <span className="subtle">
                            {attachment.filename} · {formatBytes(attachment.size)}
                          </span>
                          {attachment.part_id ? (
                            <>
                              <a
                                className="button secondary"
                                href={`/api${attachmentPath(message.id, attachment.part_id)}/content`}
                                download
                                aria-label={`Download ${attachment.filename}`}
                              >
                                <Download size={15} />
                                Download
                              </a>
                              <button
                                className="button secondary"
                                disabled={working}
                                aria-label={`Save ${attachment.filename} to Files`}
                                onClick={() =>
                                  void importAttachment(message.id, attachment.part_id!)
                                }
                              >
                                <FolderDown size={15} />
                                Save to Files
                              </button>
                              {onAnalyze ? (
                                <button
                                  className="button secondary"
                                  disabled={working}
                                  aria-label={`Analyze ${attachment.filename} in chat`}
                                  onClick={() =>
                                    void importAttachment(message.id, attachment.part_id!, true)
                                  }
                                >
                                  <MessageSquare size={15} />
                                  Analyze
                                </button>
                              ) : null}
                            </>
                          ) : (
                            <span className="subtle">
                              Refresh this thread to load attachment controls.
                            </span>
                          )}
                        </div>
                      ))}
                    </aside>
                  ) : null}
                </article>
              ))}
              <section className="draft-editor">
                <h3>Reply draft</h3>
                <EmailTemplates selectedIds={templateIds} onSelected={setTemplateIds} />
                <label>
                  Instructions for drafting
                  <input
                    value={instructions}
                    disabled={working}
                    onChange={(event) => setInstructions(event.target.value)}
                    placeholder="Optional tone or content guidance"
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={working}
                  onClick={() =>
                    void action(async () => {
                      const result = await post<{ body: string }>('/google/gmail/reply', {
                        thread_id: thread.id,
                        instructions: instructions || undefined,
                        ...(templateIds.length ? { template_ids: templateIds } : {}),
                      });
                      setBody(result.body);
                      setApproval(null);
                    }, 'Reply generated for your review')
                  }
                >
                  <Mail size={15} />
                  Generate reply
                </button>
                <label>
                  To
                  <input
                    type="email"
                    value={to}
                    disabled={working}
                    onChange={(event) => {
                      setTo(event.target.value);
                      setApproval(null);
                    }}
                  />
                </label>
                <label>
                  Subject
                  <input
                    value={subject}
                    disabled={working}
                    onChange={(event) => {
                      setSubject(event.target.value);
                      setApproval(null);
                    }}
                  />
                </label>
                <label>
                  Draft
                  <textarea
                    rows={8}
                    value={body}
                    disabled={working}
                    onChange={(event) => {
                      setBody(event.target.value);
                      setApproval(null);
                    }}
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={working}
                  onClick={() =>
                    void action(async () => {
                      setAvailableFiles(await request<LocalFile[]>('/files'));
                      setShowFiles(true);
                    })
                  }
                >
                  Attach from Files
                </button>
                {showFiles ? (
                  <fieldset>
                    <legend>Attachments · up to four files, 6 MiB total</legend>
                    {availableFiles.length ? (
                      availableFiles.map((file) => (
                        <label key={file.id}>
                          <input
                            type="checkbox"
                            checked={selectedFiles.some((item) => item.id === file.id)}
                            disabled={
                              working ||
                              (selectedFiles.length >= 4 &&
                                !selectedFiles.some((item) => item.id === file.id))
                            }
                            onChange={(event) => {
                              setApproval(null);
                              setSelectedFiles((previous) =>
                                event.target.checked
                                  ? [...previous, file]
                                  : previous.filter((item) => item.id !== file.id),
                              );
                            }}
                          />
                          {file.filename} · {formatBytes(file.size)}
                        </label>
                      ))
                    ) : (
                      <p className="subtle">Import a document into Files first.</p>
                    )}
                  </fieldset>
                ) : null}
                {selectedFiles.length ? (
                  <p className="subtle">
                    Attached: {selectedFiles.map((file) => file.filename).join(', ')}
                  </p>
                ) : null}
                <div className="row-actions">
                  <button
                    className="button secondary"
                    disabled={working || !body.trim() || !to.trim()}
                    onClick={() =>
                      void action(async () => {
                        const result = await post<{ id: string }>(
                          '/google/gmail/drafts',
                          payload(),
                        );
                        setNotice(`Gmail draft created: ${result.id}`);
                      })
                    }
                  >
                    Create Gmail draft
                  </button>
                  <button
                    className="button"
                    disabled={working || !body.trim() || !to.trim()}
                    onClick={() =>
                      void action(async () => {
                        const result = await post<{ approval: Approval }>(
                          '/google/gmail/send',
                          payload(),
                        );
                        setApproval(result.approval);
                      })
                    }
                  >
                    <Send size={15} />
                    Review send
                  </button>
                </div>
              </section>
              {approval ? (
                <ApprovalCard approval={approval} onComplete={() => void loadThreads()} />
              ) : null}
            </>
          ) : (
            <EmptyState title="Select a thread">Read an email and prepare a reply.</EmptyState>
          )}
          {working ? <Loading text="Working with Gmail…" /> : null}
        </div>
      </div>
    </div>
  );
}
