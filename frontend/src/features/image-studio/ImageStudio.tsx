import { Dice5, Download, ExternalLink, Plus, RotateCcw, Trash2, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { errorMessage, post, put, remove, request } from '../../api/client';
import {
  ConfirmDialog,
  EmptyState,
  ErrorNotice,
  Loading,
  Modal,
  formatDate,
} from '../../components/ui';
import type { Station } from '../../hooks/useStation';
import type { GeneratedImage, ImageJob, IntegrationStatus, Workflow } from '../../types';

export function ImageStudio({ station }: { station: Station }) {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflow, setWorkflow] = useState('');
  const [images, setImages] = useState<GeneratedImage[]>([]);
  const [defaultWorkflow, setDefaultWorkflow] = useState('');
  const [prompt, setPrompt] = useState('');
  const [width, setWidth] = useState(512);
  const [height, setHeight] = useState(512);
  const [seed, setSeed] = useState('');
  const [job, setJob] = useState<ImageJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [importing, setImporting] = useState(false);
  const [name, setName] = useState('');
  const [workflowJson, setWorkflowJson] = useState('');
  const [bindings, setBindings] = useState<Record<string, { node: string; input: string }>>({
    prompt: { node: '', input: 'text' },
    seed: { node: '', input: 'seed' },
    width: { node: '', input: 'width' },
    height: { node: '', input: 'height' },
  });
  const [deleting, setDeleting] = useState<GeneratedImage | null>(null);
  const [workflowDelete, setWorkflowDelete] = useState(false);
  const [selected, setSelected] = useState<GeneratedImage | null>(null);
  const workflowFile = useRef<HTMLInputElement>(null);
  async function load() {
    const results = await Promise.allSettled([
      request<IntegrationStatus>('/images/status'),
      request<{ workflows: Workflow[]; default_workflow: string }>('/images/workflows'),
      request<{ images: GeneratedImage[] }>('/images/library'),
    ]);
    if (results[0].status === 'fulfilled') setStatus(results[0].value);
    if (results[1].status === 'fulfilled') {
      setWorkflows(results[1].value.workflows);
      setDefaultWorkflow(results[1].value.default_workflow);
      setWorkflow(
        (previous) =>
          previous ||
          (results[1].status === 'fulfilled' &&
            (results[1].value.default_workflow || results[1].value.workflows[0]?.id)) ||
          '',
      );
    }
    if (results[2].status === 'fulfilled') setImages(results[2].value.images);
    const failed = results.find((result) => result.status === 'rejected');
    if (failed?.status === 'rejected') setError(errorMessage(failed.reason));
  }
  useEffect(() => {
    void load();
  }, []);
  const jobId = job?.id;
  const running =
    job && !['complete', 'completed', 'failed', 'cancelled', 'interrupted'].includes(job.status);
  useEffect(() => {
    if (!jobId || !running) return;
    let active = true;
    const interval = window.setInterval(() => {
      request<ImageJob>(`/images/jobs/${jobId}`)
        .then((next) => {
          if (!active) return;
          setJob(next);
          if (['complete', 'completed'].includes(next.status)) {
            setSelected(next.images[0] ?? null);
            void load();
          }
          if (next.error) setError(next.error);
        })
        .catch((err) => {
          if (active) {
            setError(errorMessage(err));
            setJob((previous) => (previous ? { ...previous, status: 'failed' } : previous));
          }
        });
    }, 1500);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [jobId, running]);
  async function action(run: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await run();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function generate() {
    await action(async () => {
      const next = await post<ImageJob>('/images/generate', {
        prompt,
        workflow_id: workflow || undefined,
        seed: seed ? Number(seed) : undefined,
        width,
        height,
      });
      setJob(next);
    });
  }
  async function importWorkflow() {
    await action(async () => {
      const mapped = Object.fromEntries(
        Object.entries(bindings).filter(([, value]) => value.node.trim() && value.input.trim()),
      );
      await post('/images/workflows', {
        name,
        workflow: JSON.parse(workflowJson),
        bindings: mapped,
      });
      setImporting(false);
      await load();
    });
  }
  function regenerate(image: GeneratedImage) {
    setPrompt(image.prompt);
    setSeed(String(image.seed));
    setWidth(image.width);
    setHeight(image.height);
    setWorkflow(image.workflow_id);
    setSelected(image);
    void action(async () =>
      setJob(
        await post<ImageJob>('/images/generate', {
          prompt: image.prompt,
          seed: image.seed,
          width: image.width,
          height: image.height,
          workflow_id: image.workflow_id,
        }),
      ),
    );
  }
  return (
    <div className="feature-page image-studio">
      <div className="page-heading">
        <div>
          <h1>Image Studio</h1>
          <p>Generate with your ComfyUI workflows.</p>
        </div>
        <button className="button secondary" onClick={() => setImporting(true)}>
          <Plus size={16} />
          Import workflow
        </button>
      </div>
      <ErrorNotice message={error} dismiss={() => setError('')} />
      {status && !status.available ? (
        <div className="integration-setup">
          <h3>Connect ComfyUI</h3>
          <p>{status.message}</p>
          <p className="subtle">
            Start ComfyUI, enable its API workflow export, then import an API workflow below.
          </p>
          <button className="text-button" onClick={() => station.setPage('settings')}>
            Configure the ComfyUI endpoint in Settings
          </button>
          <button className="text-button" onClick={() => void load()}>
            Check connection
          </button>
        </div>
      ) : null}
      <div className="studio-workspace">
        <form
          className="studio-controls"
          onSubmit={(event) => {
            event.preventDefault();
            void generate();
          }}
        >
          <label>
            Prompt
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={5}
              required
              placeholder="Describe the image"
            />
          </label>
          <label>
            Workflow
            <div className="workflow-select">
              <select
                aria-label="Image workflow"
                value={workflow}
                onChange={(event) => setWorkflow(event.target.value)}
              >
                <option value="">Select a workflow</option>
                {workflows.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                    {item.id === defaultWorkflow ? ' (default)' : ''}
                  </option>
                ))}
              </select>
              {workflow ? (
                <button
                  type="button"
                  className="icon-button"
                  title="Delete workflow"
                  aria-label="Delete workflow"
                  onClick={() => setWorkflowDelete(true)}
                >
                  <Trash2 size={16} />
                </button>
              ) : null}
            </div>
            {workflow && workflow !== defaultWorkflow ? (
              <button
                className="text-button"
                type="button"
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await put('/images/workflows/default', { workflow_id: workflow });
                    setDefaultWorkflow(workflow);
                  })
                }
              >
                Set as default workflow
              </button>
            ) : null}
          </label>
          <div className="form-grid">
            <label>
              Width
              <input
                type="number"
                value={width}
                min={64}
                max={4096}
                step={64}
                onChange={(event) => setWidth(Number(event.target.value))}
              />
            </label>
            <label>
              Height
              <input
                type="number"
                value={height}
                min={64}
                max={4096}
                step={64}
                onChange={(event) => setHeight(Number(event.target.value))}
              />
            </label>
          </div>
          <label>
            Seed
            <div className="seed-field">
              <input
                type="number"
                min={0}
                max={2147483647}
                value={seed}
                onChange={(event) => setSeed(event.target.value)}
                placeholder="Random"
              />
              <button
                className="icon-button"
                type="button"
                title="Random seed"
                aria-label="Random seed"
                onClick={() =>
                  setSeed(String(crypto.getRandomValues(new Uint32Array(1))[0] % 2147483647))
                }
              >
                <Dice5 size={19} />
              </button>
            </div>
          </label>
          <button
            type="submit"
            className="button"
            disabled={busy || Boolean(running) || !workflow || !prompt.trim()}
          >
            {busy ? 'Submitting…' : 'Generate image'}
          </button>
          {running ? (
            <div className="generation-progress">
              <Loading text={`${job.status} · ${Math.round(job.progress * 100)}%`} />
              <progress value={job.progress} max="1" />
              <button
                className="text-button"
                type="button"
                onClick={() =>
                  void action(async () => {
                    await remove(`/images/jobs/${job.id}`);
                    setJob({ ...job, status: 'cancelled' });
                  })
                }
              >
                <X size={14} />
                Cancel generation
              </button>
            </div>
          ) : null}
        </form>
        <div className="studio-preview">
          {selected ? (
            <>
              <img
                src={selected.content_url || `/api/images/${selected.id}/content`}
                alt={selected.prompt}
              />
              <div className="preview-header">
                <span className="subtle">
                  {selected.width} × {selected.height} · Seed {selected.seed}
                </span>
                <a
                  href={selected.content_url || `/api/images/${selected.id}/content`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink size={15} />
                  Open image
                </a>
              </div>
            </>
          ) : (
            <EmptyState title="Image preview">Generated images appear here.</EmptyState>
          )}
        </div>
      </div>
      <section className="image-library">
        <h2>
          Library <span className="subtle">{images.length} images</span>
        </h2>
        {images.length ? (
          <div className="image-grid">
            {images.map((image) => (
              <article key={image.id}>
                <button className="image-thumbnail" onClick={() => setSelected(image)}>
                  <img
                    src={image.content_url || `/api/images/${image.id}/content`}
                    alt={image.prompt}
                    loading="lazy"
                  />
                </button>
                <p>{image.prompt}</p>
                <div className="subtle">
                  {formatDate(image.created_at)} · {image.width} × {image.height}
                </div>
                <div className="subtle image-library-meta">
                  Seed {image.seed} ·{' '}
                  {workflows.find((item) => item.id === image.workflow_id)?.name ??
                    image.workflow_id}
                </div>
                <div className="row-actions">
                  <button
                    className="icon-button"
                    title="Regenerate image"
                    aria-label="Regenerate image"
                    disabled={busy || Boolean(running)}
                    onClick={() => regenerate(image)}
                  >
                    <RotateCcw size={15} />
                  </button>
                  <a
                    className="icon-button"
                    href={image.content_url || `/api/images/${image.id}/content`}
                    download
                    title="Download image"
                    aria-label="Download image"
                  >
                    <Download size={15} />
                  </a>
                  <button
                    className="icon-button"
                    title="Delete image"
                    aria-label="Delete image"
                    onClick={() => setDeleting(image)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted">No generated images yet.</p>
        )}
      </section>
      {importing ? (
        <Modal title="Import ComfyUI API workflow" onClose={() => setImporting(false)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void importWorkflow();
            }}
          >
            <label>
              Workflow name
              <input value={name} onChange={(event) => setName(event.target.value)} required />
            </label>
            <input
              hidden
              type="file"
              accept=".json,application/json"
              ref={workflowFile}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file)
                  void file
                    .text()
                    .then(setWorkflowJson)
                    .catch((err) => setError(errorMessage(err)));
              }}
            />
            <button
              className="button secondary"
              type="button"
              onClick={() => workflowFile.current?.click()}
            >
              Select API workflow JSON
            </button>
            <label>
              API workflow JSON
              <textarea
                value={workflowJson}
                onChange={(event) => setWorkflowJson(event.target.value)}
                rows={5}
                required
              />
            </label>
            <p className="subtle">
              Bind the node IDs and input names to your workflow. Prompt is required; other bindings
              are optional.
            </p>
            {Object.entries(bindings).map(([key, value]) => (
              <div className="binding-row" key={key}>
                <span>{key}</span>
                <input
                  aria-label={`${key} node ID`}
                  required={key === 'prompt'}
                  placeholder="Node ID"
                  value={value.node}
                  onChange={(event) =>
                    setBindings({ ...bindings, [key]: { ...value, node: event.target.value } })
                  }
                />
                <input
                  aria-label={`${key} input name`}
                  required={key === 'prompt'}
                  placeholder="Input name"
                  value={value.input}
                  onChange={(event) =>
                    setBindings({ ...bindings, [key]: { ...value, input: event.target.value } })
                  }
                />
              </div>
            ))}
            <ErrorNotice message={error} />
            <div className="modal-actions">
              <button className="button" type="submit" disabled={busy}>
                Import workflow
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
      {deleting ? (
        <ConfirmDialog
          title="Delete generated image?"
          busy={busy}
          onClose={() => setDeleting(null)}
          confirm={() =>
            void action(async () => {
              await remove(`/images/${deleting.id}`);
              if (selected?.id === deleting.id) setSelected(null);
              setDeleting(null);
              await load();
            })
          }
        >
          {deleting.prompt}
        </ConfirmDialog>
      ) : null}
      {workflowDelete ? (
        <ConfirmDialog
          title="Delete workflow?"
          busy={busy}
          onClose={() => setWorkflowDelete(false)}
          confirm={() =>
            void action(async () => {
              await remove(`/images/workflows/${workflow}`);
              setWorkflow('');
              setWorkflowDelete(false);
              await load();
            })
          }
        >
          Remove this workflow from Pixel Station.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
