import { Check, X } from 'lucide-react';
import { useState } from 'react';
import { errorMessage, post, remove } from '../../api/client';
import { ErrorNotice } from '../../components/ui';
import type { Approval } from '../../types';

export function ApprovalCard({
  approval,
  onComplete,
}: {
  approval: Approval;
  onComplete?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  async function act(confirm: boolean) {
    setBusy(true);
    setError('');
    try {
      if (confirm)
        await post(`/integrations/approvals/${approval.id}/confirm`, { confirmed: true });
      else await remove(`/integrations/approvals/${approval.id}`);
      setResult(confirm ? 'Action completed' : 'Action cancelled');
      onComplete?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="approval-card">
      <h3>{approval.action.replaceAll('_', ' ')}</h3>
      {result ? (
        <p role="status">{result}</p>
      ) : (
        <>
          <p className="subtle">Review this exact action before it is sent to Google.</p>
          <pre className="code-block">{JSON.stringify(approval.payload, null, 2)}</pre>
          <ErrorNotice message={error} />
          <div className="row-actions">
            <button className="button" disabled={busy} onClick={() => void act(true)}>
              <Check size={15} />
              {busy ? 'Working…' : 'Confirm action'}
            </button>
            <button className="button secondary" disabled={busy} onClick={() => void act(false)}>
              <X size={15} />
              Reject
            </button>
          </div>
        </>
      )}
    </div>
  );
}
