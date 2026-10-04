import { Check, X } from 'lucide-react';
import { useState } from 'react';
import { errorMessage, post, remove } from '../../api/client';
import { ErrorNotice, formatBytes } from '../../components/ui';
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
  const {
    connection_binding: rawBinding,
    attachments: rawAttachments,
    ...reviewPayload
  } = approval.payload;
  const attachments = Array.isArray(rawAttachments)
    ? rawAttachments.filter(
        (value): value is Record<string, unknown> => value && typeof value === 'object',
      )
    : [];
  const binding =
    rawBinding && typeof rawBinding === 'object' ? (rawBinding as Record<string, unknown>) : null;
  const account =
    binding?.account && typeof binding.account === 'object'
      ? (binding.account as Record<string, unknown>)
      : null;
  const service = approval.action.startsWith('gmail_') ? 'gmail' : 'calendar';
  const accountLabel =
    binding?.service === service &&
    typeof binding.generation === 'string' &&
    binding.generation &&
    typeof account?.label === 'string' &&
    account.label.trim()
      ? account.label
      : null;
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
          {accountLabel ? (
            <p>
              Account: <strong>{accountLabel}</strong>
            </p>
          ) : (
            <p className="notice">
              Account not recorded. Reconnect the service and review a new proposal before
              confirming.
            </p>
          )}
          <pre className="code-block">{JSON.stringify(reviewPayload, null, 2)}</pre>
          {attachments.length ? (
            <section aria-label="Reviewed email attachments">
              <h4>Attachments</h4>
              {attachments.map((attachment, index) => (
                <div key={index}>
                  <strong>{String(attachment.filename ?? 'Unnamed attachment')}</strong>
                  <p className="subtle">
                    {formatBytes(Number(attachment.size ?? 0))} ·{' '}
                    {String(attachment.media_type ?? '')}
                  </p>
                  <pre className="code-block">
                    SHA-256: {String(attachment.sha256 ?? 'Missing digest')}
                  </pre>
                </div>
              ))}
            </section>
          ) : null}
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
