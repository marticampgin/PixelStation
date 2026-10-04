import { Plus, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { errorMessage, post } from '../../api/client';
import { ErrorNotice, Modal } from '../../components/ui';
import type { FileEditProposal } from './FileEditCard';
import './fileEdits.css';

export interface EditTarget {
  location: string;
  text: string;
  section: string;
  in_table: boolean;
}

export interface TargetedDocument {
  file_id: string;
  filename: string;
  before_sha256: string;
  targets: EditTarget[];
  scope: string;
  warning: string;
}

export interface TextChange {
  location: string;
  before: string;
  after: string;
}

export function TargetedDocxEditor({
  document,
  onClose,
  onProposed,
}: {
  document: TargetedDocument;
  onClose: () => void;
  onProposed: (proposal: FileEditProposal) => void;
}) {
  const first = document.targets[0];
  const [changes, setChanges] = useState<TextChange[]>([
    { location: first.location, before: first.text, after: first.text },
  ]);
  const [plan, setPlan] = useState('Update the reviewed contract fields');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);

  function update(index: number, patch: Partial<TextChange>) {
    setChanges((current) =>
      current.map((change, number) => (number === index ? { ...change, ...patch } : change)),
    );
  }

  async function review() {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError('');
    try {
      const proposal = await post<FileEditProposal>(
        `/files/${document.file_id}/targeted-edit-proposals`,
        { before_sha256: document.before_sha256, plan, changes },
        controller.signal,
      );
      if (!controller.signal.aborted) onProposed(proposal);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err));
    } finally {
      pending.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <Modal title={`Edit ${document.filename}`} onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void review();
        }}
      >
        <p className="subtle">{document.scope}</p>
        <div className="notice">{document.warning}</div>
        <p className="subtle">
          Select a paragraph, then replace a unique text fragment such as a date or package name.
          Tables, headers, and footers can also be selected.
        </p>
        <label>
          Edit plan
          <input value={plan} onChange={(event) => setPlan(event.target.value)} required />
        </label>
        {changes.map((change, index) => (
          <fieldset key={index} disabled={busy} className="targeted-edit-fields">
            <legend>Replacement {index + 1}</legend>
            <label>
              Paragraph {index + 1}
              <select
                value={change.location}
                onChange={(event) => {
                  const selected = document.targets.find(
                    (target) => target.location === event.target.value,
                  );
                  if (selected)
                    update(index, {
                      location: selected.location,
                      before: selected.text,
                      after: selected.text,
                    });
                }}
              >
                {document.targets.map((target, number) => (
                  <option key={target.location} value={target.location}>
                    {number + 1} · {target.section}
                    {target.in_table ? ' table' : ''} · {target.text.slice(0, 90)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Exact current text {index + 1}
              <textarea
                aria-label={`Exact current text ${index + 1}`}
                rows={3}
                value={change.before}
                maxLength={10000}
                required
                onChange={(event) => update(index, { before: event.target.value })}
              />
              <small>Must occur exactly once in the selected paragraph.</small>
            </label>
            <label>
              Replacement text {index + 1}
              <textarea
                rows={3}
                value={change.after}
                maxLength={10000}
                onChange={(event) => update(index, { after: event.target.value })}
              />
            </label>
            <button
              type="button"
              className="text-button"
              disabled={changes.length === 1}
              onClick={() =>
                setChanges((current) => current.filter((_, number) => number !== index))
              }
              aria-label={`Remove replacement ${index + 1}`}
            >
              <Trash2 size={14} /> Remove
            </button>
          </fieldset>
        ))}
        <button
          type="button"
          className="text-button"
          disabled={busy || changes.length >= 32}
          onClick={() =>
            setChanges((current) => [
              ...current,
              { location: first.location, before: first.text, after: first.text },
            ])
          }
        >
          <Plus size={14} /> Add replacement
        </button>
        <ErrorNotice message={error} />
        <div className="modal-actions">
          <button
            className="button"
            disabled={
              busy || changes.some((change) => !change.before || change.before === change.after)
            }
            type="submit"
          >
            {busy ? 'Preparing review…' : 'Review file changes'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
