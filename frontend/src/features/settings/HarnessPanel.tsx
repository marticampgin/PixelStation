import { Download, Play, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { errorMessage, post, request } from '../../api/client';
import { Disclosure } from '../../components/Disclosure';
import { ErrorNotice, Loading, formatDate } from '../../components/ui';
import type { Settings } from '../../types';
import type { Evaluation, Watchtower } from './harnessTypes';
import { HarnessReports, type HarnessReport } from './HarnessReports';
import './harness.css';

const duration = (value: number | null | undefined) =>
  value == null
    ? 'Unavailable'
    : value >= 1000
      ? `${(value / 1000).toFixed(2)} s`
      : `${Math.round(value)} ms`;

export function HarnessPanel({
  settings,
  onSettings,
}: {
  settings: Settings;
  onSettings: (next: Settings) => void;
}) {
  const [data, setData] = useState<Watchtower | null>(null);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [native, setNative] = useState(false);
  const [pokerNative, setPokerNative] = useState(false);
  const [privateDetails, setPrivateDetails] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    const next = await request<Watchtower>('/harness');
    setData(next);
    const latest = next.reports.find((row): row is Evaluation => row.report.kind === 'evaluation');
    if (latest) setEvaluation(latest);
  }, []);
  useEffect(() => {
    void load().catch((err) => setError(errorMessage(err)));
  }, [load]);
  useEffect(() => {
    if (!evaluation || evaluation.report.status !== 'RUNNING') return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await request<Evaluation>(`/harness/evaluations/${evaluation.id}`);
        if (!active) return;
        setEvaluation(next);
        if (next.report.status === 'RUNNING') timer = setTimeout(() => void poll(), 1000);
        else await load();
      } catch (err) {
        if (active) {
          setError(errorMessage(err));
          timer = setTimeout(() => void poll(), 1000);
        }
      }
    };
    timer = setTimeout(() => void poll(), 500);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [evaluation?.id, evaluation?.report.status, load]);
  const running = evaluation?.report.status === 'RUNNING';
  const nativeScopes = [
    evaluation?.report.native_requested ? 'chat model' : '',
    evaluation?.report.poker_native_requested ? 'Poker strategy' : '',
  ]
    .filter(Boolean)
    .join(' and ');
  async function execute(kind: 'report' | 'evaluation') {
    setBusy(true);
    setError('');
    try {
      if (kind === 'evaluation')
        setEvaluation(
          await post<Evaluation>('/harness/evaluations', { native, poker_native: pokerNative }),
        );
      else {
        await post('/harness/run');
        await load();
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  const metrics = data?.metrics;
  const pokerStrategy = metrics?.poker_strategy;
  return (
    <div className="watchtower">
      <div className="watchtower-heading">
        <div>
          <h2>Local watchtower</h2>
          <p className="subtle">
            Recorded outcomes and repeatable checks. Nothing is sent to a telemetry service.
          </p>
        </div>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => void load().catch((err) => setError(errorMessage(err)))}
        >
          <RefreshCw size={15} /> Refresh
        </button>
      </div>
      <ErrorNotice message={error} dismiss={() => setError('')} />
      <section className="section">
        <h3>Passive observations</h3>
        {!metrics ? (
          <Loading />
        ) : (
          <>
            <div className="watchtower-stats">
              <article>
                <strong>{metrics.sample_count}</strong>
                <span>Recorded runs · {metrics.window_days} days</span>
                <small>
                  {metrics.instrumented_runs} with detailed metrics
                  {metrics.capped ? ` · capped from ${metrics.matching_runs}` : ''}
                </small>
              </article>
              <article>
                <strong>
                  {metrics.completion_rate == null
                    ? '—'
                    : `${(metrics.completion_rate * 100).toFixed(0)}%`}
                </strong>
                <span>Workflow completion</span>
                <small>
                  {metrics.outcomes.complete ?? 0} of {metrics.terminal_runs} terminal runs
                </small>
              </article>
              <article>
                <strong>{duration(metrics.latency_ms.p95)}</strong>
                <span>Response latency p95</span>
                <small>
                  {metrics.latency_ms.sample_count} samples · median{' '}
                  {duration(metrics.latency_ms.median)}
                </small>
              </article>
              <article>
                <strong>{duration(metrics.first_public_token_ms.median)}</strong>
                <span>First public token median</span>
                <small>
                  {metrics.first_public_token_ms.sample_count} samples · includes queue/tool work
                </small>
              </article>
            </div>
            <p className="watchtower-outcomes">
              Errors <b>{metrics.outcomes.error ?? 0}</b> · Cancelled{' '}
              <b>{metrics.outcomes.interrupted ?? 0}</b> · Repairs <b>{metrics.repair_attempts}</b>{' '}
              · Fallbacks <b>{metrics.fallbacks}</b>
            </p>
            <p className="subtle">{metrics.interpretation}</p>
            <Disclosure
              title="Latency by route and model"
              preference="harness-latency-open"
              initiallyOpen={false}
            >
              <div className="watchtower-table">
                <table>
                  <thead>
                    <tr>
                      <th>Route / model</th>
                      <th>Samples</th>
                      <th>Median</th>
                      <th>p95</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.groups.slice(0, 20).map((group) => (
                      <tr key={`${group.route}:${group.model}`}>
                        <td>
                          {group.route}
                          <small>{group.model ?? 'No model'}</small>
                        </td>
                        <td>{group.sample_count}</td>
                        <td>{duration(group.latency_ms.median)}</td>
                        <td>{duration(group.latency_ms.p95)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!metrics.groups.length && (
                  <p className="muted">No completed timing samples yet.</p>
                )}
              </div>
            </Disclosure>
            <Disclosure
              title="Native token measurements and budget use"
              preference="harness-budget-open"
              initiallyOpen={false}
            >
              <p>
                Prompt tokens:{' '}
                {metrics.native_usage.prompt_token_samples
                  ? metrics.native_usage.prompt_tokens
                  : 'Unavailable'}{' '}
                ({metrics.native_usage.prompt_token_samples} native samples). Generated tokens:{' '}
                {metrics.native_usage.generation_token_samples
                  ? metrics.native_usage.generated_tokens
                  : 'Unavailable'}{' '}
                ({metrics.native_usage.generation_token_samples} native samples).
              </p>
              <p>
                Generation throughput median:{' '}
                {metrics.native_usage.tokens_per_second.median == null
                  ? 'Unavailable'
                  : `${metrics.native_usage.tokens_per_second.median.toFixed(1)} tokens/s`}{' '}
                · {metrics.native_usage.tokens_per_second.sample_count} measured samples.
                Denominator is Ollama eval duration.
              </p>
              <p className="subtle">
                {metrics.native_usage.note} Context and public output below use character-based
                token estimates.
              </p>
              <div role="group" aria-label="Production Poker decisions">
                <p>
                  <strong>Production Poker decisions</strong>
                </p>
                {pokerStrategy && pokerStrategy.sample_count > 0 ? (
                  <>
                    <div className="watchtower-problems">
                      <span>
                        Measured decisions: <b>{pokerStrategy.sample_count}</b>
                      </span>
                      <span>
                        Native without fallback: <b>{pokerStrategy.native_without_fallback}</b>
                      </span>
                      <span>
                        Fallback decisions: <b>{pokerStrategy.fallback_decisions}</b>
                      </span>
                    </div>
                    <p>
                      Final selected actions:{' '}
                      {Object.entries(pokerStrategy.actions)
                        .map(([action, count]) => `${action.replaceAll('_', '-')}: ${count}`)
                        .join(' · ') || 'Not recorded'}
                      .
                    </p>
                    <p>
                      {pokerStrategy.preflop_samples > 0 ? (
                        <>
                          Selected preflop all-ins: {pokerStrategy.preflop_all_ins} across{' '}
                          {pokerStrategy.preflop_samples} preflop decisions. Raw model preflop
                          all-in attempts: {pokerStrategy.raw_preflop_all_in_attempts}.
                        </>
                      ) : (
                        'Preflop observations unavailable: no measured preflop decisions.'
                      )}
                    </p>
                    <p>Validation rejections: {pokerStrategy.validation_rejections}.</p>
                    <p className="subtle">{pokerStrategy.note}</p>
                  </>
                ) : (
                  <p className="muted">Poker observations unavailable: no measured decisions.</p>
                )}
                <p className="subtle">
                  Newly instrumented production decisions only. Synthetic strategy scenarios are
                  reported separately in Active evaluations.
                </p>
              </div>
              <div className="watchtower-table">
                <table>
                  <thead>
                    <tr>
                      <th>Recent run</th>
                      <th>Context estimate</th>
                      <th>Memories</th>
                      <th>Output estimate</th>
                      <th>Tool steps</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.budgets.slice(0, 10).map((row) => (
                      <tr key={row.id}>
                        <td>
                          {row.route}
                          <small>{row.id.slice(0, 8)}</small>
                        </td>
                        <td>
                          {row.context_tokens_estimated
                            ? `${row.context_tokens_estimated.total} / ${row.context_tokens_estimated.budget}`
                            : row.route === 'poker_bot'
                              ? 'Not recorded'
                              : '—'}
                        </td>
                        <td>
                          {row.route === 'poker_bot' ? (
                            'Not applicable'
                          ) : (
                            <>
                              {row.retrieved_memories ?? '—'} /{' '}
                              {row.configuration?.retrieval_count ?? '—'}
                            </>
                          )}
                        </td>
                        <td>
                          {row.public_output_tokens_estimated ??
                            (row.route === 'poker_bot' ? 'Not recorded' : '—')}
                          <small>
                            {row.route === 'poker_bot' ? (
                              row.generation_tokens_per_attempt != null ? (
                                <>
                                  Recorded per-attempt generation ceiling{' '}
                                  {row.generation_tokens_per_attempt} tokens
                                </>
                              ) : (
                                'Per-attempt generation ceiling not recorded'
                              )
                            ) : (
                              <>
                                Configured chat answer ceiling{' '}
                                {row.configuration?.bounded_response_tokens ?? 'Not recorded'}
                              </>
                            )}
                          </small>
                        </td>
                        <td>
                          {row.route === 'poker_bot' ? (
                            'Not applicable'
                          ) : (
                            <>
                              {row.tool_steps == null
                                ? 'Unmeasured direct workflow'
                                : row.tool_steps}
                              <small>Research ceiling {row.configuration?.max_steps ?? '—'}</small>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="subtle">
                Maximum steps applies to research/search-fetch and selected source reads. It is not
                a universal tool or inference quota. Poker memory and research limits do not apply;
                recorded Poker generation ceilings apply per attempt. Missing historical ceilings
                remain unavailable. Native usage is aggregated above.
              </p>
            </Disclosure>
            <Disclosure
              title="Recent problems and friction"
              preference="harness-problems-open"
              initiallyOpen={false}
            >
              <div className="watchtower-problems">
                {Object.entries(metrics.problem_counts).map(([kind, count]) => (
                  <span key={kind}>
                    {kind.replaceAll('_', ' ')} <b>{count}</b>
                  </span>
                ))}
              </div>
              {metrics.recent_problem_runs.map((row) => (
                <article className="watchtower-problem" key={row.id}>
                  <strong>
                    {row.route} · {row.status}
                  </strong>
                  <span>
                    {duration(row.latency_ms)} · {formatDate(row.started_at)}
                  </span>
                  <code>{row.id}</code>
                </article>
              ))}
              {!metrics.recent_problem_runs.length && (
                <p className="muted">
                  No error, cancellation, repair or fallback runs in this sample.
                </p>
              )}
            </Disclosure>
          </>
        )}
      </section>
      <section className="section">
        <h3>Active evaluations</h3>
        <p className="subtle">
          Versioned fixtures exercise real core behavior in temporary data. They do not prove the
          quality of your model, Web, Google or image services.
        </p>
        <label className="toggle-field">
          <input
            type="checkbox"
            checked={native}
            disabled={busy || running}
            onChange={(event) => setNative(event.target.checked)}
          />{' '}
          Include native model probes (manual, up to 90 seconds; uses local inference)
        </label>
        <label className="toggle-field">
          <input
            type="checkbox"
            checked={pokerNative}
            disabled={busy || running}
            onChange={(event) => setPokerNative(event.target.checked)}
          />{' '}
          Include Poker strategy probes (manual, up to 90 seconds; uses local inference)
        </label>
        <p className="subtle">
          Native chat and Poker strategy scopes each allow up to 90 seconds; selecting both allows
          up to 180 seconds of local inference. Probes run only after you click Run evaluations.
        </p>
        <div className="row-actions">
          <button
            className="button"
            disabled={busy || running}
            onClick={() => void execute('evaluation')}
          >
            <Play size={15} />
            {running ? 'Evaluation running…' : 'Run evaluations'}
          </button>
          <button
            className="button secondary"
            disabled={busy || running}
            onClick={() => void execute('report')}
          >
            Save passive report
          </button>
        </div>
        {evaluation ? (
          <div className="watchtower-evaluation">
            <div className="watchtower-evaluation-heading">
              <strong>
                {evaluation.report.status}
                {evaluation.report.outcome ? ` · ${evaluation.report.outcome}` : ''}
              </strong>
              <small>
                {evaluation.report.fixture?.version}
                {evaluation.report.runner_version == null
                  ? ''
                  : ` · runner ${evaluation.report.runner_version}`}{' '}
                · {formatDate(evaluation.created_at)}
              </small>
            </div>
            {(evaluation.report.native_requested != null ||
              evaluation.report.poker_native_requested != null) && (
              <p className="subtle">Requested native scopes: {nativeScopes || 'none'}.</p>
            )}
            <div className="watchtower-gates">
              {Object.entries(evaluation.report.counts ?? {}).map(([status, count]) => (
                <span className={`gate-${status.toLowerCase()}`} key={status}>
                  {status} {count}
                </span>
              ))}
            </div>
            {evaluation.report.cases?.map((item) => (
              <div key={item.id} className={`watchtower-case gate-${item.status.toLowerCase()}`}>
                <Disclosure
                  title={
                    <>
                      <strong>{item.status}</strong>{' '}
                      <span className="watchtower-case-label">{item.label}</span>{' '}
                      <small className="watchtower-case-meta">
                        {item.duration_ms == null ? item.scope : duration(item.duration_ms)}
                      </small>
                    </>
                  }
                  preference={`harness-case-${item.id}-open`}
                  initiallyOpen={false}
                >
                  {item.reason && <p>{item.reason}</p>}
                  {item.error_type && <p>Error: {item.error_type}</p>}
                  <pre className="code-block">
                    {JSON.stringify(item.measurements ?? {}, null, 2)}
                  </pre>
                </Disclosure>
              </div>
            ))}
            {running && (
              <p role="status">
                Running isolated gates
                {nativeScopes ? `, then explicitly requested ${nativeScopes} probes` : ''}…
              </p>
            )}
            {evaluation.report.baseline_id && (
              <p className="subtle">
                Compared with {evaluation.report.baseline_id.slice(0, 8)} using the same fixture
                {evaluation.report.runner_version == null
                  ? ' and configuration.'
                  : ', runner version and configuration.'}{' '}
                {evaluation.report.comparison?.filter((item) => item.regressed).length ?? 0}{' '}
                critical regressions. A single latency comparison has no statistical significance.
              </p>
            )}
          </div>
        ) : (
          <p className="muted">No active evaluations recorded yet.</p>
        )}
      </section>
      <section className="section">
        <h3>Failure patterns and regression candidates</h3>
        <p className="subtle">
          Exact groups use recorded route, model and structured failure evidence. Private examples
          are available only through the diagnostic export opt-in.
        </p>
        <HarnessReports
          reports={
            data?.reports.filter((row): row is HarnessReport => row.report.kind !== 'evaluation') ??
            null
          }
        />
      </section>
      <section className="section">
        <h3>Limits and evidence</h3>
        {Object.entries(data?.defaults ?? {}).map(([name, value]) => (
          <p key={name}>
            <strong>
              {name.replaceAll('_', ' ')}: default {value.default}.
            </strong>{' '}
            {value.reason}
          </p>
        ))}
        <p className="subtle">
          Tune against repeatable native gates and observed failures/latency, changing one limit at
          a time. There is no universal performance score or automatic source modification.
        </p>
        <div className="form-grid">
          <label className="toggle-field">
            <input
              type="checkbox"
              checked={settings.harness_enabled}
              onChange={(event) =>
                onSettings({ ...settings, harness_enabled: event.target.checked })
              }
            />{' '}
            Save scheduled passive reports
          </label>
          <label>
            Report interval (hours)
            <input
              type="number"
              min={1}
              max={168}
              value={settings.harness_interval_hours}
              onChange={(event) =>
                onSettings({ ...settings, harness_interval_hours: Number(event.target.value) })
              }
            />
          </label>
          <label>
            Observation window (days)
            <input
              type="number"
              min={1}
              max={90}
              value={Number(settings.harness_window_days ?? 7)}
              onChange={(event) =>
                onSettings({ ...settings, harness_window_days: Number(event.target.value) })
              }
            />
          </label>
          <label>
            Maximum recent samples
            <input
              type="number"
              min={100}
              max={5000}
              value={Number(settings.harness_sample_limit ?? 1000)}
              onChange={(event) =>
                onSettings({ ...settings, harness_sample_limit: Number(event.target.value) })
              }
            />
          </label>
        </div>
        <p className="subtle">
          Use Save settings above to apply these settings. The scheduled watchtower computes
          statistics only; active native probes are never scheduled.
        </p>
      </section>
      <section className="section">
        <h3>Diagnostics export</h3>
        <p>
          Configuration, versions, measured aggregates and correlation IDs are included. Prompts,
          responses and source bodies are omitted by default.
        </p>
        <label className="toggle-field">
          <input
            type="checkbox"
            checked={privateDetails}
            onChange={(event) => setPrivateDetails(event.target.checked)}
          />{' '}
          Include bounded private local details
        </label>
        {privateDetails && (
          <p className="subtle">
            This includes private prompts, responses and traces where stored. Review the file before
            sharing.
          </p>
        )}
        <a
          className="button secondary"
          href={`/api/harness/diagnostics${privateDetails ? '?include_content=true' : ''}`}
          download
        >
          <Download size={15} /> Download diagnostics JSON
        </a>
      </section>
    </div>
  );
}
