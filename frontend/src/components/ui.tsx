import { AlertCircle, LoaderCircle, X } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';

export function PixelMark({ large = false }: { large?: boolean }) {
  return (
    <img
      className={`pixel-mark ${large ? 'large' : ''}`}
      src="/assets/fox-logo.png"
      alt=""
      aria-hidden="true"
    />
  );
}

export function ErrorNotice({
  message,
  dismiss,
}: {
  message?: string | null;
  dismiss?: () => void;
}) {
  return message ? (
    <div className="notice error" role="alert">
      <AlertCircle size={17} />
      <span>{message}</span>
      {dismiss ? (
        <button className="icon-button" aria-label="Dismiss error" onClick={dismiss}>
          <X size={16} />
        </button>
      ) : null}
    </div>
  ) : null;
}

export function Loading({ text = 'Loading…' }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={18} />
      {text}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <PixelMark large />
      <h2>{title}</h2>
      {children ? <div className="muted">{children}</div> : null}
    </div>
  );
}

export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      aria-labelledby={titleId}
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal-title">
        <h2 id={titleId}>{title}</h2>
        <button onClick={onClose} className="icon-button" aria-label="Close dialog">
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function ConfirmDialog({
  title,
  children,
  confirm,
  onClose,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  confirm: () => void;
  onClose: () => void;
  busy?: boolean;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <div className="modal-body">{children}</div>
      <div className="modal-actions">
        <button className="button secondary" onClick={onClose}>
          Cancel
        </button>
        <button className="button danger" disabled={busy} onClick={confirm}>
          {busy ? 'Working…' : 'Confirm'}
        </button>
      </div>
    </Modal>
  );
}

export const formatDate = (value: string) =>
  new Date(
    value.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`,
  ).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
export const formatBytes = (bytes: number) =>
  bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 ** 2
      ? `${(bytes / 1024).toFixed(1)} KB`
      : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
export const modelLabel = (name: string) => {
  if (name === 'pixel-station-lfm2.5:2.6b') return 'LFM2.5 · 2.6B · Local alias';
  const registry = /^LiquidAI\/lfm2\.5-2\.6b(?::(.+))?$/i.exec(name);
  if (registry)
    return `LFM2.5 · 2.6B · Ollama${registry[1] && registry[1] !== 'latest' ? ` · ${registry[1]}` : ''}`;
  const imported = /^hf\.co\/LiquidAI\/LFM2\.5-2\.6B-GGUF(?::(.+))?$/i.exec(name);
  if (imported)
    return `LFM2.5 · 2.6B · HF import${imported[1] && imported[1] !== 'Q4_K_M' ? ` · ${imported[1]}` : ''}`;
  return name;
};
