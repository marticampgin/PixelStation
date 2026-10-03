import { Check, Download, X } from 'lucide-react';
import { useState } from 'react';
import { errorMessage, post, remove } from '../../api/client';
import { ErrorNotice } from '../../components/ui';
import type { LocalFile } from '../../types';

export interface FileEditProposal {
  id: string;
  file_id: string;
  filename: string;
  preview_content: string;
  plan: string;
  scope?: string;
  warning?: string;
}

export function FileEditCard({ proposal }: { proposal: FileEditProposal }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  const [revision, setRevision] = useState('');
  async function act(confirm: boolean) {
    setBusy(true);
    setError('');
    try {
      if (confirm) {
        const file = await post<LocalFile & { revision_id: string }>(
          `/files/edit-proposals/${proposal.id}/confirm`,
          { confirmed: true },
        );
        setRevision(file.revision_id);
      } else await remove(`/files/edit-proposals/${proposal.id}`);
      setResult(confirm ? 'File updated' : 'Edit rejected');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="approval-card file-edit-card">
      <h3>{proposal.filename}</h3>
      {result ? (
        <>
          <p role="status">{result}</p>
          {revision ? (
            <div className="row-actions">
              <a href={`/api/files/${proposal.file_id}/content`} className="text-button" download>
                <Download size={14} />
                Download updated file
              </a>
              <a
                href={`/api/files/${proposal.file_id}/revisions/${revision}/content`}
                className="text-button"
                download
              >
                Download original revision
              </a>
            </div>
          ) : null}
        </>
      ) : (
        <>
          <p className="edit-plan">{proposal.plan}</p>
          {proposal.warning ? <div className="notice">{proposal.warning}</div> : null}
          <details open>
            <summary className="subtle">Review replacement content</summary>
            <pre className="code-block edit-preview">{proposal.preview_content}</pre>
          </details>
          <ErrorNotice message={error} />
          <div className="row-actions">
            <button className="button" disabled={busy} onClick={() => void act(true)}>
              <Check size={15} />
              {busy ? 'Working…' : 'Confirm file edit'}
            </button>
            <button className="button secondary" disabled={busy} onClick={() => void act(false)}>
              <X size={15} />
              Reject edit
            </button>
          </div>
        </>
      )}
    </div>
  );
}
