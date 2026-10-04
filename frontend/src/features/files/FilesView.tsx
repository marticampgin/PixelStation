import {
  Copy,
  Download,
  FileText,
  MessageSquare,
  Pencil,
  Plus,
  Search,
  Trash2,
  Upload,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { core } from '../../api/services';
import { errorMessage, post, request } from '../../api/client';
import {
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Loading,
  Modal,
  formatBytes,
  formatDate,
} from '../../components/ui';
import { useResource } from '../../hooks/useResource';
import type { Station } from '../../hooks/useStation';
import type { LocalFile } from '../../types';
import { FileEditReview, type FileEditProposal } from './FileEditCard';
import { TargetedDocxEditor, type TargetedDocument } from './TargetedDocxEditor';

const loadFiles = () => core.files();
export function FilesView({ station }: { station: Station }) {
  const resource = useResource(loadFiles);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<LocalFile | null>(null);
  const [creating, setCreating] = useState(false);
  const [copying, setCopying] = useState<LocalFile | null>(null);
  const [copyFilename, setCopyFilename] = useState('');
  const [filename, setFilename] = useState('document');
  const [content, setContent] = useState('');
  const [format, setFormat] = useState('md');
  const [editing, setEditing] = useState<{
    file_id: string;
    filename: string;
    format: string;
    content: string;
    scope?: string;
    warning?: string;
  } | null>(null);
  const [editPlan, setEditPlan] = useState('');
  const [targetedEditing, setTargetedEditing] = useState<TargetedDocument | null>(null);
  const [proposal, setProposal] = useState<FileEditProposal | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const files =
    resource.data?.filter((file) => file.filename.toLowerCase().includes(search.toLowerCase())) ??
    [];
  async function mutate(action: () => Promise<unknown>) {
    setBusy(true);
    resource.setError(null);
    try {
      await action();
      await resource.refresh();
      setDeleting(null);
      setCreating(false);
    } catch (err) {
      resource.setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function upload(items: FileList | null) {
    if (!items) return;
    await mutate(async () => {
      for (const file of items) await core.upload(file);
    });
    if (input.current) input.current.value = '';
  }
  async function openEdit(file: LocalFile) {
    setBusy(true);
    try {
      if (file.extension === '.docx') {
        setTargetedEditing(await request(`/files/${file.id}/edit-targets`));
      } else {
        setEditing(await request(`/files/${file.id}/edit-content`));
        setEditPlan('Update document content');
      }
    } catch (err) {
      resource.setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function reviewEdit() {
    if (!editing) return;
    setBusy(true);
    try {
      const next = await post<typeof proposal>(`/files/${editing.file_id}/edit-proposals`, {
        content: editing.content,
        plan: editPlan,
      });
      setProposal(next);
      setEditing(null);
    } catch (err) {
      resource.setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="feature-page">
      <div className="page-heading">
        <div>
          <h1>Files</h1>
          <p>Uploaded and generated documents.</p>
        </div>
        <div className="row-actions">
          <button className="button secondary" onClick={() => setCreating(true)}>
            <Plus size={16} />
            Create
          </button>
          <button className="button" disabled={busy} onClick={() => input.current?.click()}>
            <Upload size={16} />
            Import
          </button>
          <input
            ref={input}
            type="file"
            hidden
            multiple
            onChange={(event) => void upload(event.target.files)}
          />
        </div>
      </div>
      <ErrorNotice message={resource.error} dismiss={() => resource.setError(null)} />
      <div className="toolbar">
        <label className="search-field">
          <Search size={18} />
          <input
            aria-label="Search files"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search files"
          />
        </label>
      </div>
      {resource.loading ? (
        <Loading />
      ) : files.length ? (
        <div className="file-list">
          {files.map((file) => (
            <div key={file.id} className="file-row">
              <div className="file-icon">
                <FileText size={25} />
              </div>
              <div className="file-info">
                <a href={`/api/files/${file.id}/content`} target="_blank" rel="noreferrer">
                  {file.filename}
                </a>
                <div className="subtle">
                  {file.extension?.replace('.', '').toUpperCase()} · {formatBytes(file.size)} ·{' '}
                  {formatDate(file.created_at)} · {file.source}
                </div>
                {file.parse_error ? (
                  <div className="parse-error">{file.parse_error}</div>
                ) : (
                  <div className="subtle">
                    {file.parse_status}
                    {file.parser ? ` · ${file.parser}` : ''}
                  </div>
                )}
              </div>
              <div className="row-actions">
                <button
                  className="icon-button"
                  title="Make a copy"
                  aria-label={`Make a copy of ${file.filename}`}
                  disabled={busy}
                  onClick={() => {
                    setCopying(file);
                    setCopyFilename(file.filename.replace(/(\.[^.]+)$/, ' copy$1'));
                  }}
                >
                  <Copy size={17} />
                </button>
                {['txt', 'md', 'csv', 'xlsx', 'docx', 'pdf'].includes(
                  file.extension.replace('.', ''),
                ) ? (
                  <button
                    className="icon-button"
                    title="Edit file"
                    aria-label={`Edit ${file.filename}`}
                    disabled={busy}
                    onClick={() => void openEdit(file)}
                  >
                    <Pencil size={17} />
                  </button>
                ) : null}
                <a
                  className="icon-button"
                  href={`/api/files/${file.id}/content`}
                  download
                  title="Download"
                  aria-label={`Download ${file.filename}`}
                >
                  <Download size={17} />
                </a>
                <button
                  className="icon-button"
                  title="Analyze in chat"
                  aria-label={`Analyze ${file.filename} in chat`}
                  onClick={() =>
                    station.beginChat(`Summarize the attached file ${file.filename}.`, [file])
                  }
                >
                  <MessageSquare size={17} />
                </button>
                <button
                  className="icon-button"
                  title="Delete file"
                  aria-label={`Delete ${file.filename}`}
                  onClick={() => setDeleting(file)}
                >
                  <Trash2 size={17} />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title={search ? 'No matching files' : 'No files yet'}>
          Import a document or attach one in chat.
        </EmptyState>
      )}
      {editing ? (
        <Modal title={`Edit ${editing.filename}`} onClose={() => setEditing(null)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void reviewEdit();
            }}
          >
            {editing.scope ? <p className="subtle">Edit scope: {editing.scope}</p> : null}
            {editing.warning ? <div className="notice">{editing.warning}</div> : null}
            <label>
              Edit plan
              <input
                value={editPlan}
                onChange={(event) => setEditPlan(event.target.value)}
                required
              />
            </label>
            <label>
              Content
              <textarea
                rows={12}
                value={editing.content}
                onChange={(event) => setEditing({ ...editing, content: event.target.value })}
              />
            </label>
            <ErrorNotice message={resource.error} />
            <div className="modal-actions">
              <button className="button" disabled={busy} type="submit">
                Review file changes
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
      {targetedEditing ? (
        <TargetedDocxEditor
          document={targetedEditing}
          onClose={() => setTargetedEditing(null)}
          onProposed={(next) => {
            setTargetedEditing(null);
            setProposal(next);
          }}
        />
      ) : null}
      {proposal ? (
        <ConfirmDialog
          title={`Replace ${proposal.filename}?`}
          busy={busy}
          onClose={() => setProposal(null)}
          confirm={() =>
            void mutate(async () => {
              await post(`/files/edit-proposals/${proposal.id}/confirm`, { confirmed: true });
              setProposal(null);
            })
          }
        >
          <p>{proposal.plan}</p>
          {proposal.scope ? <p className="subtle">Edit scope: {proposal.scope}</p> : null}
          {proposal.warning ? <div className="notice">{proposal.warning}</div> : null}
          <p className="subtle">
            The original file must still match the reviewed version. Confirm to replace its library
            content.
          </p>
          <FileEditReview proposal={proposal} />
          <ErrorNotice message={resource.error} />
        </ConfirmDialog>
      ) : null}
      {deleting ? (
        <ConfirmDialog
          title="Delete file?"
          busy={busy}
          onClose={() => setDeleting(null)}
          confirm={() => void mutate(() => core.deleteFile(deleting.id))}
        >
          {deleting.filename} will be removed from your local library.
        </ConfirmDialog>
      ) : null}
      {copying ? (
        <Modal title="Make a library copy" onClose={() => setCopying(null)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(async () => {
                await post(`/files/${copying.id}/copy`, { filename: copyFilename });
                setCopying(null);
              });
            }}
          >
            <p className="subtle">
              Give this copy its own name before editing. The source document stays unchanged.
            </p>
            <label>
              Filename for copy
              <input
                value={copyFilename}
                maxLength={150}
                onChange={(event) => setCopyFilename(event.target.value)}
                required
              />
            </label>
            <ErrorNotice message={resource.error} />
            <div className="modal-actions">
              <button className="button" disabled={busy} type="submit">
                {busy ? 'Copying…' : 'Make a copy'}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
      {creating ? (
        <Modal title="Create document" onClose={() => setCreating(false)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(() =>
                core.createFile(`${filename.replace(/\.[^.]+$/, '')}.${format}`, content, format),
              );
            }}
          >
            <div className="form-grid">
              <label>
                Filename
                <input
                  value={filename}
                  onChange={(event) => setFilename(event.target.value)}
                  required
                />
              </label>
              <label>
                Format
                <select value={format} onChange={(event) => setFormat(event.target.value)}>
                  {['md', 'txt', 'csv', 'xlsx', 'docx', 'pdf'].map((value) => (
                    <option value={value} key={value}>
                      {value.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>
              <label className="full">
                Content
                {format === 'csv' || format === 'xlsx' ? (
                  <small>Enter CSV rows to create cells.</small>
                ) : null}
                <textarea
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  rows={8}
                  required
                />
              </label>
            </div>
            <ErrorNotice message={resource.error} />
            <div className="modal-actions">
              <button className="button" disabled={busy} type="submit">
                {busy ? 'Creating…' : 'Create document'}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </div>
  );
}
