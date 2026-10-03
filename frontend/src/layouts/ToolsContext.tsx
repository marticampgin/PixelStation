import { useEffect, useState } from 'react';
import { errorMessage, request } from '../api/client';
import { ErrorNotice, Loading } from '../components/ui';
import { ApprovalCard } from '../features/google/ApprovalCard';
import type { Approval, GoogleStatus, IntegrationStatus, Message } from '../types';

interface ToolMetadata {
  id: string;
  display_name: string;
  category: string;
  permission: string;
  enabled: boolean;
}
export function ToolsContext({ message, busy }: { message?: Message; busy: boolean }) {
  const [tools, setTools] = useState<ToolMetadata[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [statuses, setStatuses] = useState<{ name: string; message: string; available: boolean }[]>(
    [],
  );
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true);
    const route = message?.traces.find((trace) => trace.route)?.route as
      { intent?: string } | undefined;
    Promise.allSettled([
      request<ToolMetadata[]>(`/tools?intent=${encodeURIComponent(route?.intent ?? '')}`),
      request<{ approvals: Approval[] }>('/integrations/approvals'),
      request<IntegrationStatus>('/web/status'),
      request<IntegrationStatus>('/images/status'),
      request<GoogleStatus>('/google/status'),
    ]).then((results) => {
      if (!active) return;
      if (results[0].status === 'fulfilled') setTools(results[0].value);
      if (results[1].status === 'fulfilled') setApprovals(results[1].value.approvals);
      const statusRows = [];
      if (results[2].status === 'fulfilled')
        statusRows.push({ name: 'SearXNG', ...results[2].value });
      if (results[3].status === 'fulfilled')
        statusRows.push({ name: 'ComfyUI', ...results[3].value });
      if (results[4].status === 'fulfilled')
        statusRows.push({
          name: 'Google',
          message: results[4].value.message,
          available: results[4].value.connected,
        });
      setStatuses(statusRows);
      const failed = results.find((result) => result.status === 'rejected');
      if (failed?.status === 'rejected') setError(errorMessage(failed.reason));
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, [message?.id, busy]);
  return (
    <>
      <section>
        <h3>Integrations</h3>
        {loading ? (
          <Loading text="Checking providers…" />
        ) : (
          statuses.map((status) => (
            <div className="tool-status" key={status.name}>
              <strong>{status.name}</strong>
              <span className={status.available ? 'success-text' : 'muted'}>
                {status.available ? 'Available' : 'Setup required'}
              </span>
              <p>{status.message}</p>
            </div>
          ))
        )}
        <ErrorNotice message={error} />
      </section>
      {approvals.length ? (
        <section>
          <h3>Awaiting your review</h3>
          {approvals.map((approval) => (
            <ApprovalCard
              approval={approval}
              key={approval.id}
              onComplete={() =>
                setApprovals((previous) => previous.filter((item) => item.id !== approval.id))
              }
            />
          ))}
        </section>
      ) : null}
      <section>
        <h3>Relevant tools</h3>
        {tools.length ? (
          tools.map((tool) => (
            <div className="relevant-tool" key={tool.id}>
              <strong>{tool.display_name}</strong>
              <span>
                {tool.permission?.replaceAll('_', ' ')}
                {tool.enabled === false ? ' · disabled' : ''}
              </span>
            </div>
          ))
        ) : (
          <p className="muted">No tools needed for this response.</p>
        )}
      </section>
    </>
  );
}
