import { Copy, Download, RefreshCw, Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { core } from '../../api/services';
import { errorMessage, post, request } from '../../api/client';
import { ConfirmDialog, ErrorNotice, Loading, modelLabel } from '../../components/ui';
import type { Station } from '../../hooks/useStation';
import type { IntegrationStatus, Settings } from '../../types';
import { GoogleSetup } from '../google/GoogleSetup';
import { SearxSetup } from '../web/SearxSetup';
import { HarnessPanel } from './HarnessPanel';
import {
  acceptsRole,
  liteModel,
  modelSetupCommand,
  preferredLiteModel,
  roleCapability,
} from './modelRoles';

const tabs = ['Models', 'Image generation', 'Web', 'Google', 'Memory', 'Agent', 'Harness', 'Data'];
const presets: Record<string, { primary: string; embedding: string; context: number }> = {
  lite: {
    primary: liteModel,
    embedding: 'qwen3-embedding:0.6b',
    context: 8192,
  },
  balanced: { primary: 'qwen3.5:4b', embedding: 'qwen3-embedding:0.6b', context: 16384 },
  strong: { primary: 'qwen3.5:9b-q4_K_M', embedding: 'qwen3-embedding:0.6b', context: 16384 },
};
export function SettingsView({ station }: { station: Station }) {
  const [tab, setTab] = useState('Models');
  const [settings, setSettings] = useState<Settings | null>(station.settings);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [cacheConfirm, setCacheConfirm] = useState(false);
  const [dataPath, setDataPath] = useState('');
  useEffect(() => {
    core
      .settings()
      .then(setSettings)
      .catch((err) => setError(errorMessage(err)));
  }, []);
  useEffect(() => {
    if (tab === 'Data')
      void request<{ path: string }>('/data')
        .then((value) => setDataPath(value.path))
        .catch((err) => setError(errorMessage(err)));
  }, [tab]);
  function update(key: keyof Settings, value: unknown) {
    if (settings) setSettings({ ...settings, [key]: value });
  }
  async function action(run: () => Promise<unknown>, success = '') {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await run();
      setNotice(success);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!settings) return;
    await action(async () => {
      const next = await core.saveSettings(settings);
      station.setSettings(next);
      setSettings(next);
      await station.refreshModels();
    }, 'Settings saved');
  }
  async function testIntegration(name: 'images' | 'web') {
    await action(async () => {
      if (!settings) return;
      await core.saveSettings(settings).then(station.setSettings);
      const result = await request<IntegrationStatus>(`/${name}/status`);
      if (!result.available) throw new Error(result.message);
      setNotice(result.message || 'Connection available');
    });
  }
  function applyPreset(profile: string) {
    if (!settings) return;
    const preset = presets[profile];
    const primary = profile === 'lite' ? preferredLiteModel(station.models.models) : preset.primary;
    setSettings({
      ...settings,
      profile,
      context_tokens: preset.context,
      roles: {
        ...settings.roles,
        primary_chat: primary,
        planner: primary,
        router: primary,
        summarizer: primary,
        memory_extractor: primary,
        critic: primary,
        vision: profile === 'lite' ? 'qwen3.5:2b' : preset.primary,
        embedding: preset.embedding,
      },
    });
  }
  const textField = (key: keyof Settings, label: string, hint?: string) => (
    <label>
      {label}
      <input
        value={String(settings?.[key] ?? '')}
        onChange={(event) => update(key, event.target.value)}
      />
      {hint ? <small>{hint}</small> : null}
    </label>
  );
  const numberField = (key: keyof Settings, label: string, min: number, max: number) => (
    <label>
      {label}
      <input
        type="number"
        min={min}
        max={max}
        value={Number(settings?.[key] ?? min)}
        onChange={(event) => update(key, Number(event.target.value))}
      />
    </label>
  );
  const toggle = (key: keyof Settings, label: string) => (
    <label className="toggle-field">
      <input
        type="checkbox"
        checked={Boolean(settings?.[key])}
        onChange={(event) => update(key, event.target.checked)}
      />
      {label}
    </label>
  );
  return (
    <div className="feature-page settings-page">
      <div className="page-heading">
        <h1>Settings</h1>
        <button className="button" disabled={busy || !settings} onClick={() => void save()}>
          <Save size={16} />
          {busy ? 'Working…' : 'Save settings'}
        </button>
      </div>
      <div className="settings-tabs">
        {tabs.map((name) => (
          <button
            key={name}
            className={tab === name ? 'active' : ''}
            onClick={() => {
              setTab(name);
              setNotice('');
              setError('');
            }}
          >
            {name}
          </button>
        ))}
      </div>
      <ErrorNotice message={error} dismiss={() => setError('')} />
      {notice ? (
        <div className="notice" role="status">
          {notice}
        </div>
      ) : null}
      {!settings ? (
        <Loading />
      ) : (
        <div className="settings-content">
          {tab === 'Models' ? (
            <>
              <section className="section">
                <h2>Ollama connection</h2>
                <div className="form-grid">
                  {textField('ollama_url', 'Endpoint')}
                  {numberField('context_tokens', 'Context token budget', 2048, 65536)}
                </div>
                <div className="connection-row">
                  <span className="subtle">
                    {station.models.available
                      ? `${station.models.models.length} installed models`
                      : (station.models.error ?? 'Ollama is unavailable')}
                  </span>
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() =>
                      void action(async () => {
                        await core.saveSettings(settings).then(station.setSettings);
                        await station.refreshModels();
                      })
                    }
                  >
                    <RefreshCw size={15} />
                    Refresh models
                  </button>
                </div>
              </section>
              <section className="section">
                <h2>Model profile</h2>
                <div className="profile-options">
                  {Object.entries(presets).map(([profile, preset]) => (
                    <button
                      key={profile}
                      className={settings.profile === profile ? 'selected' : ''}
                      onClick={() => applyPreset(profile)}
                    >
                      <strong>{profile[0].toUpperCase() + profile.slice(1)}</strong>
                      <span>
                        {modelLabel(
                          profile === 'lite'
                            ? preferredLiteModel(station.models.models)
                            : preset.primary,
                        )}
                      </span>
                      <small>{(preset.context / 1024).toFixed(0)}K context</small>
                    </button>
                  ))}
                </div>
                <p className="subtle">
                  Presets assign roles. Download missing models using the commands below.
                </p>
              </section>
              <section className="section">
                <h2>Role assignments</h2>
                <div className="form-grid">
                  {Object.entries(settings.roles).map(([role, assigned]) => {
                    const installed = station.models.models.find(
                      (model) => model.name === assigned,
                    );
                    const compatible = station.models.models.filter((model) =>
                      acceptsRole(model, role),
                    );
                    const setupCommand = modelSetupCommand(assigned);
                    return (
                      <label key={role}>
                        {role.replaceAll('_', ' ')}
                        <select
                          value={assigned}
                          onChange={(event) =>
                            setSettings({
                              ...settings,
                              roles: { ...settings.roles, [role]: event.target.value },
                            })
                          }
                        >
                          <option value="">Not assigned</option>
                          {assigned && !installed ? (
                            <option value={assigned}>{modelLabel(assigned)} · missing</option>
                          ) : null}
                          {installed && !acceptsRole(installed, role) ? (
                            <option value={assigned} disabled>
                              {modelLabel(assigned)} · incompatible
                            </option>
                          ) : null}
                          {compatible.map((model) => (
                            <option value={model.name} key={model.name}>
                              {modelLabel(model.name)}
                            </option>
                          ))}
                        </select>
                        {assigned && !installed ? (
                          <div className="pull-command">
                            <code>{setupCommand}</code>
                            <button
                              className="icon-button"
                              aria-label={`Copy pull command for ${assigned}`}
                              onClick={() =>
                                void action(
                                  () => navigator.clipboard.writeText(setupCommand),
                                  'Command copied',
                                )
                              }
                            >
                              <Copy size={13} />
                            </button>
                          </div>
                        ) : null}
                        {installed && !acceptsRole(installed, role) ? (
                          <small>
                            This model does not advertise {roleCapability(role)} capability. Select
                            a compatible model.
                          </small>
                        ) : null}
                      </label>
                    );
                  })}
                </div>
              </section>
              <section className="section">
                <h2>Resource behavior</h2>
                {textField(
                  'keep_alive',
                  'Model keep-alive',
                  'For example 5m to keep a model warm; 0 unloads after a request.',
                )}
              </section>
            </>
          ) : null}
          {tab === 'Image generation' ? (
            <section className="section">
              <h2>ComfyUI</h2>
              {textField('comfyui_url', 'Endpoint')}
              <div className="connection-row">
                <span className="subtle">
                  Import API workflows and manage the image library in Image Studio.
                </span>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void testIntegration('images')}
                >
                  Test connection
                </button>
              </div>
              <button className="text-button" onClick={() => station.setPage('images')}>
                Open Image Studio
              </button>
            </section>
          ) : null}
          {tab === 'Web' ? (
            <section className="section">
              <h2>SearXNG</h2>
              {textField('searxng_url', 'Endpoint')}
              <SearxSetup />
              <div className="connection-row">
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void testIntegration('web')}
                >
                  Test connection
                </button>
                <button className="text-button" onClick={() => station.setPage('web')}>
                  Open Web
                </button>
              </div>
            </section>
          ) : null}
          {tab === 'Google' ? (
            <>
              <GoogleSetup />
              <div className="section" style={{ marginTop: 30 }}>
                {textField('time_zone', 'Calendar time zone')}
              </div>
            </>
          ) : null}
          {tab === 'Memory' ? (
            <section className="section">
              <h2>Memory and summaries</h2>
              <div className="settings-toggles">
                {toggle('auto_memory', 'Automatic memory extraction')}
              </div>
              <div className="form-grid">
                {numberField('retrieval_count', 'Maximum retrieved memories', 1, 8)}
                {numberField('summary_turns', 'Summarize after this many turns', 2, 30)}
              </div>
              <p className="subtle">Memories retain their sources and can be edited in Memory.</p>
            </section>
          ) : null}
          {tab === 'Agent' ? (
            <section className="section">
              <h2>Bounded execution</h2>
              <div className="form-grid">
                {numberField('max_steps', 'Maximum tool steps', 1, 12)}
              </div>
              <div className="settings-toggles">
                {toggle('critic_enabled', 'Enable one critic pass for complex answers')}
              </div>
              <p className="subtle">
                Email sending and Calendar changes always require a confirmation card. The
                application validates tool calls and enforces the step limit.
              </p>
            </section>
          ) : null}
          {tab === 'Harness' ? <HarnessPanel settings={settings} onSettings={setSettings} /> : null}
          {tab === 'Data' ? (
            <section className="section">
              <h2>Local data</h2>
              {dataPath ? <code className="code-block">{dataPath}</code> : null}
              <p className="subtle">
                Data is stored in the configured Pixel Station data directory. Export and backup
                include your private local data.
              </p>
              <div className="row-actions">
                <a className="button secondary" href="/api/data/export" download>
                  <Download size={16} />
                  Export data
                </a>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      const result = await post<Record<string, unknown>>('/data/backup');
                      setNotice(
                        `Backup created: ${String(result.filename ?? JSON.stringify(result))}`,
                      );
                    })
                  }
                >
                  Create backup
                </button>
                <button className="button secondary" onClick={() => setCacheConfirm(true)}>
                  Clear cache
                </button>
              </div>
            </section>
          ) : null}
        </div>
      )}
      {cacheConfirm ? (
        <ConfirmDialog
          title="Clear cached data?"
          busy={busy}
          onClose={() => setCacheConfirm(false)}
          confirm={() =>
            void action(async () => {
              await post('/data/clear-cache', { confirmed: true });
              setCacheConfirm(false);
            }, 'Cache cleared')
          }
        >
          Cached provider results will be removed. They can be rebuilt as needed.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
