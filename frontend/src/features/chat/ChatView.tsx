import {
  Archive,
  Copy,
  FileDown,
  MoreHorizontal,
  Paperclip,
  Pencil,
  RotateCcw,
  Send,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { core } from '../../api/services';
import { errorMessage } from '../../api/client';
import { ConfirmDialog, EmptyState, Loading, Modal, modelLabel } from '../../components/ui';
import type { Station } from '../../hooks/useStation';
import type { Message } from '../../types';
import { TraceArtifacts } from './TraceArtifacts';

function MessageActions({ message, station }: { message: Message; station: Station }) {
  const [notice, setNotice] = useState('');
  async function act(action: () => Promise<unknown>, success: string) {
    try {
      await action();
      setNotice(success);
    } catch (err) {
      station.setError(errorMessage(err));
    }
  }
  return (
    <div className="message-actions">
      <button
        className="icon-button"
        title="Copy response"
        aria-label="Copy response"
        onClick={() => act(() => navigator.clipboard.writeText(message.content), 'Copied')}
      >
        <Copy size={15} />
      </button>
      <button
        className="icon-button"
        title="Save as Markdown"
        aria-label="Save response to file"
        onClick={() =>
          act(
            () => core.createFile(`response-${message.id.slice(0, 8)}.md`, message.content, 'md'),
            'Saved to Files',
          )
        }
      >
        <FileDown size={15} />
      </button>
      <button
        className="icon-button"
        title="Useful response"
        aria-label="Useful response"
        onClick={() => act(() => station.feedback(message.id, 'up'), 'Feedback saved')}
      >
        <ThumbsUp size={15} />
      </button>
      <button
        className="icon-button"
        title="Report a problem"
        aria-label="Report a problem with response"
        onClick={() => act(() => station.feedback(message.id, 'down'), 'Feedback saved')}
      >
        <ThumbsDown size={15} />
      </button>
      <span className="muted">{notice || (message.model ? modelLabel(message.model) : '')}</span>
    </div>
  );
}

export function ChatView({ station }: { station: Station }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const [uploading, setUploading] = useState(false);
  const [menu, setMenu] = useState(false);
  const [dialog, setDialog] = useState<'rename' | 'delete' | null>(null);
  const [title, setTitle] = useState('');
  useEffect(() => {
    scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' });
  }, [station.streaming, station.conversation?.messages.length]);
  useEffect(() => {
    if (composerInput.current) {
      composerInput.current.style.height = '38px';
      composerInput.current.style.height = `${Math.min(160, composerInput.current.scrollHeight)}px`;
    }
  }, [station.draft]);
  async function upload(files: FileList | null) {
    if (!files) return;
    setUploading(true);
    for (const file of files) {
      try {
        const record = await core.upload(file);
        station.setAttachments((previous) => [...previous, record]);
      } catch (err) {
        station.setError(errorMessage(err));
      }
    }
    setUploading(false);
    if (fileInput.current) fileInput.current.value = '';
  }
  async function mutate(action: () => Promise<unknown>) {
    try {
      await action();
      await station.refreshConversations();
      setDialog(null);
    } catch (err) {
      station.setError(errorMessage(err));
    }
  }
  const messages =
    station.conversation?.messages.filter((message) => message.role !== 'system') ?? [];
  return (
    <div className="chat-view">
      <div className="chat-scroll" ref={scroll}>
        {station.loadingChat ? (
          <Loading text="Opening conversation…" />
        ) : messages.length ? (
          <div className="messages">
            {station.conversationId ? (
              <div className="conversation-controls">
                <button
                  className="icon-button"
                  title="Conversation actions"
                  aria-label="Conversation actions"
                  onClick={() => setMenu((value) => !value)}
                >
                  <MoreHorizontal size={19} />
                </button>
                {menu ? (
                  <div className="dropdown">
                    <button
                      onClick={() => {
                        setTitle(station.conversation!.title);
                        setDialog('rename');
                        setMenu(false);
                      }}
                    >
                      <Pencil size={15} />
                      Rename
                    </button>
                    <button
                      onClick={() => {
                        void mutate(() =>
                          core.updateConversation(station.conversationId!, {
                            archived: !station.conversation!.archived,
                          }),
                        ).then(station.newChat);
                        setMenu(false);
                      }}
                    >
                      <Archive size={15} />
                      {station.conversation?.archived ? 'Restore' : 'Archive'}
                    </button>
                    <button
                      onClick={() => {
                        setDialog('delete');
                        setMenu(false);
                      }}
                    >
                      <Trash2 size={15} />
                      Delete
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
            {messages.map((message) => (
              <article key={message.id} className={`message ${message.role}`}>
                <div className="message-byline">
                  {message.role === 'user' ? 'You' : 'Pixel Station'}
                  <time>
                    {new Date(message.created_at).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </time>
                </div>
                <div className="markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                </div>
                <TraceArtifacts message={message} />
                {message.attachment_ids.length ? (
                  <small className="muted">
                    {message.attachment_ids.length} attachment
                    {message.attachment_ids.length > 1 ? 's' : ''}
                  </small>
                ) : null}
                {message.role === 'assistant' ? (
                  <MessageActions message={message} station={station} />
                ) : null}
              </article>
            ))}
            {station.streaming ? (
              <article className="message assistant streaming">
                <div className="message-byline">Pixel Station</div>
                <div className="markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{station.streaming}</ReactMarkdown>
                </div>
              </article>
            ) : null}
            {station.status ? <Loading text={station.status} /> : null}
          </div>
        ) : (
          <EmptyState title="New chat">Send a message to begin.</EmptyState>
        )}
      </div>
      <div className="composer-area">
        {station.webSources.length ? (
          <div className="attachment-chips">
            {station.webSources.map((source) => (
              <div key={source.url}>
                {source.title || new URL(source.url).hostname}
                <button
                  className="icon-button"
                  aria-label={`Remove source ${source.title || source.url}`}
                  onClick={() =>
                    station.setWebSources((previous) =>
                      previous.filter((item) => item.url !== source.url),
                    )
                  }
                >
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        {messages.some((message) => message.role === 'assistant') && !station.busy ? (
          <button className="regenerate text-button" onClick={() => void station.send(true)}>
            <RotateCcw size={14} />
            Regenerate
          </button>
        ) : null}
        {station.attachments.length ? (
          <div className="attachment-chips">
            {station.attachments.map((file) => (
              <div key={file.id}>
                <Paperclip size={13} />
                {file.filename}
                <button
                  className="icon-button"
                  aria-label={`Remove attachment ${file.filename}`}
                  onClick={() =>
                    station.setAttachments((previous) =>
                      previous.filter((item) => item.id !== file.id),
                    )
                  }
                >
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            void station.send();
          }}
        >
          <input
            type="file"
            ref={fileInput}
            hidden
            multiple
            onChange={(event) => void upload(event.target.files)}
          />
          <button
            type="button"
            className="attach-button icon-button"
            title="Attach files"
            aria-label="Attach files"
            disabled={uploading || station.busy}
            onClick={() => fileInput.current?.click()}
          >
            <Paperclip size={22} />
          </button>
          <textarea
            ref={composerInput}
            aria-label="Message Pixel Station"
            placeholder={uploading ? 'Importing attachment…' : 'Message Pixel Station'}
            value={station.draft}
            onChange={(event) => station.setDraft(event.target.value)}
            rows={1}
            disabled={station.busy}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void station.send();
              }
            }}
          />
          {station.busy ? (
            <button
              type="button"
              className="send-button"
              aria-label="Stop generation"
              onClick={station.cancel}
            >
              <Square size={19} />
            </button>
          ) : (
            <button
              type="submit"
              className="send-button"
              aria-label="Send message"
              disabled={!station.draft.trim() || uploading}
            >
              <Send size={21} />
            </button>
          )}
        </form>
        <div className="composer-hint">
          {station.busy ? 'Generating response' : 'Enter to send'}
        </div>
      </div>
      {dialog === 'rename' ? (
        <Modal title="Rename conversation" onClose={() => setDialog(null)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(() => core.updateConversation(station.conversationId!, { title }));
            }}
          >
            <label>
              Title
              <input
                autoFocus
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
              />
            </label>
            <div className="modal-actions">
              <button className="button" type="submit">
                Save
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
      {dialog === 'delete' ? (
        <ConfirmDialog
          title="Delete conversation?"
          onClose={() => setDialog(null)}
          confirm={() =>
            void mutate(() => core.deleteConversation(station.conversationId!)).then(
              station.newChat,
            )
          }
        >
          This removes this conversation and its messages.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
