import { formatDate } from '../../components/ui';

export interface HarnessReport {
  id: string;
  created_at: string;
  report: {
    kind?: string;
    window_days: number;
    event_count: number;
    findings?: { kind: string; count: number; proposal: string; examples?: string[] }[];
    regression_candidates: Record<string, unknown>[];
    patterns?: {
      id: string;
      kind: string;
      route: string | null;
      model: string | null;
      status: string | null;
      count: number;
      run_count: number;
      run_ids: string[];
      error_codes: string[];
      error_types: string[];
      failed_tools: string[];
      stages: string[];
      validations: string[];
      classification: string;
      recommendation: string;
      regression_hint: string;
      links_capped: boolean;
      private_examples?: string[];
    }[];
    pattern_count?: number;
    patterns_capped?: boolean;
    interpretation?: string;
  };
}

export function HarnessReports({ reports }: { reports: HarnessReport[] | null }) {
  if (!reports?.length) return <p className="muted">No passive reports yet.</p>;
  return (
    <div className="harness-reports">
      {reports.map((row, index) => (
        <details className="report" key={row.id} open={index === 0}>
          <summary>
            <span>{formatDate(row.created_at)}</span>
            <span className="subtle">
              {row.report.event_count == null
                ? 'Event count unavailable'
                : `${row.report.event_count} friction events`}{' '}
              ·{' '}
              {row.report.window_days == null
                ? 'Window unavailable'
                : `${row.report.window_days} days`}
            </span>
          </summary>
          <div className="harness-report-body">
            <p className="subtle">
              {row.report.interpretation ??
                'Observed workflow outcomes identify investigation candidates; they do not grade answer quality or establish root cause.'}
            </p>
            {row.report.patterns?.length ? (
              row.report.patterns.map((pattern) => (
                <article className="harness-finding" key={pattern.id}>
                  <div className="finding-heading">
                    <h3>{pattern.kind.replaceAll('_', ' ')}</h3>
                    <span className="subtle">
                      {pattern.count} event{pattern.count === 1 ? '' : 's'} · {pattern.run_count}{' '}
                      linked run{pattern.run_count === 1 ? '' : 's'}
                    </span>
                  </div>
                  <p>
                    {pattern.route ?? 'Route unavailable'} · {pattern.model ?? 'Model unavailable'}{' '}
                    · {pattern.status ?? 'Status unavailable'}
                  </p>
                  <dl>
                    <dt>Error codes</dt>
                    <dd>{pattern.error_codes.join(', ') || 'Unclassified'}</dd>
                    <dt>Error types</dt>
                    <dd>{pattern.error_types.join(', ') || 'Unavailable'}</dd>
                    <dt>Failed tools</dt>
                    <dd>{pattern.failed_tools.join(', ') || 'Unavailable'}</dd>
                    <dt>Recorded stages</dt>
                    <dd>{pattern.stages.join(', ') || 'Unavailable'}</dd>
                    <dt>Associated validations</dt>
                    <dd>{pattern.validations.join(', ') || 'None recorded'}</dd>
                  </dl>
                  <p>{pattern.recommendation}</p>
                  <details className="finding-examples">
                    <summary>Regression candidate</summary>
                    <p>{pattern.regression_hint}</p>
                  </details>
                  {pattern.run_ids.length ? (
                    <details className="finding-examples">
                      <summary>Inspect linked local runs</summary>
                      <div className="row-actions">
                        {pattern.run_ids.map((id) => (
                          <a
                            key={id}
                            className="button secondary"
                            href={`/api/harness/runs/${encodeURIComponent(id)}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Run {id.slice(0, 8)}
                          </a>
                        ))}
                      </div>
                      {pattern.links_capped ? (
                        <p className="subtle">
                          At most 20 run and event links are included for this pattern.
                        </p>
                      ) : null}
                    </details>
                  ) : (
                    <p className="subtle">No run correlation was recorded for these events.</p>
                  )}
                  {pattern.private_examples?.length ? (
                    <details className="finding-examples">
                      <summary>Private examples</summary>
                      {pattern.private_examples.map((example, index) => (
                        <pre className="code-block" key={index}>
                          {example}
                        </pre>
                      ))}
                    </details>
                  ) : null}
                </article>
              ))
            ) : row.report.findings?.length ? (
              row.report.findings.map((finding) => (
                <article className="harness-finding" key={finding.kind}>
                  <div className="finding-heading">
                    <h3>{finding.kind.replaceAll('_', ' ')}</h3>
                    <span className="subtle">
                      {finding.count} occurrence{finding.count === 1 ? '' : 's'}
                    </span>
                  </div>
                  <p>{finding.proposal}</p>
                  {finding.examples?.filter(Boolean).length ? (
                    <details className="finding-examples">
                      <summary>Examples</summary>
                      {finding.examples.filter(Boolean).map((example, i) => (
                        <pre className="code-block" key={i}>
                          {example}
                        </pre>
                      ))}
                    </details>
                  ) : null}
                </article>
              ))
            ) : (
              <p className="muted">No friction events were recorded during this period.</p>
            )}
            {row.report.patterns_capped ? (
              <p className="subtle">
                Showing the 100 largest of {row.report.pattern_count} patterns in this capped event
                sample.
              </p>
            ) : null}
            {row.report.regression_candidates?.length ? (
              <details className="finding-examples">
                <summary>
                  {row.report.regression_candidates.length} suggested regression case
                  {row.report.regression_candidates.length === 1 ? '' : 's'}
                </summary>
                <pre className="code-block">
                  {JSON.stringify(row.report.regression_candidates, null, 2)}
                </pre>
              </details>
            ) : null}
          </div>
        </details>
      ))}
    </div>
  );
}
