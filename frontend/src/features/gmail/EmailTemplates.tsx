import { useState } from 'react';
import { ApiError, errorMessage, post, request } from '../../api/client';
import { ErrorNotice } from '../../components/ui';
import { useResource } from '../../hooks/useResource';

export type EmailTemplate = {
  id: string;
  name: string;
  body: string;
  keywords: string[];
  approved: boolean;
  review_sha256: string;
};
const loadTemplates = () => request<EmailTemplate[]>('/google/gmail/templates');

export function EmailTemplates({
  selectedIds,
  onSelected,
}: {
  selectedIds: string[];
  onSelected: (ids: string[]) => void;
}) {
  const templates = useResource(loadTemplates);
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [body, setBody] = useState('');
  const [keywords, setKeywords] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function action(run: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await run();
      await templates.refresh();
    } catch (err) {
      setError(errorMessage(err));
      if (err instanceof ApiError && err.status === 409) await templates.refresh();
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="section">
      <summary>Reply templates</summary>
      <p className="subtle">
        Only wording you review and approve can be used. Changes require approval again. Matching
        approved templates can be retrieved automatically; select up to three to choose specific
        wording.
      </p>
      <ErrorNotice message={error || templates.error} />
      {templates.data?.map((template) => (
        <div key={template.id} className="section">
          <label>
            <input
              type="checkbox"
              checked={selectedIds.includes(template.id)}
              disabled={
                !template.approved ||
                busy ||
                (selectedIds.length >= 3 && !selectedIds.includes(template.id))
              }
              onChange={(event) =>
                onSelected(
                  event.target.checked
                    ? [...selectedIds, template.id]
                    : selectedIds.filter((id) => id !== template.id),
                )
              }
            />{' '}
            {template.name} · {template.approved ? 'Approved' : 'Needs review'}
          </label>
          <p className="email-body">{template.body}</p>
          <p className="subtle">Matching keywords: {template.keywords.join(', ') || 'None'}</p>
          <div className="row-actions">
            <button
              type="button"
              className="button secondary"
              disabled={busy}
              onClick={() => {
                setEditing(template.id);
                setName(template.name);
                setBody(template.body);
                setKeywords(template.keywords.join(', '));
              }}
            >
              Edit template
            </button>
            {!template.approved ? (
              <button
                type="button"
                className="button secondary"
                disabled={busy || !template.review_sha256}
                onClick={() =>
                  void action(() =>
                    post(`/google/gmail/templates/${template.id}/approve`, {
                      confirmed: true,
                      review_sha256: template.review_sha256,
                    }),
                  )
                }
              >
                Approve this wording
              </button>
            ) : null}
          </div>
        </div>
      ))}
      <label>
        Template name
        <input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} />
      </label>
      <label>
        Matching keywords
        <input
          value={keywords}
          placeholder="rental, booking, leieavtale"
          onChange={(event) => setKeywords(event.target.value)}
        />
      </label>
      <label>
        Template wording
        <textarea
          value={body}
          rows={5}
          maxLength={6000}
          onChange={(event) => setBody(event.target.value)}
        />
      </label>
      <div className="row-actions">
        <button
          type="button"
          className="button secondary"
          disabled={busy || !name.trim() || !body.trim()}
          onClick={() =>
            void action(async () => {
              const input = {
                name: name.trim(),
                body: body.trim(),
                keywords: keywords
                  .split(',')
                  .map((word) => word.trim())
                  .filter(Boolean),
              };
              await request(
                editing ? `/google/gmail/templates/${editing}` : '/google/gmail/templates',
                { method: editing ? 'PUT' : 'POST', body: JSON.stringify(input) },
              );
              if (editing) onSelected(selectedIds.filter((id) => id !== editing));
              setEditing(null);
              setName('');
              setBody('');
              setKeywords('');
            })
          }
        >
          {editing ? 'Save changes for review' : 'Save for review'}
        </button>
        {editing ? (
          <button
            type="button"
            className="button secondary"
            onClick={() => {
              setEditing(null);
              setName('');
              setBody('');
              setKeywords('');
            }}
          >
            Cancel template edit
          </button>
        ) : null}
      </div>
    </details>
  );
}
