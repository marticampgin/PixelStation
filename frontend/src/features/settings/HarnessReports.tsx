import { formatDate } from '../../components/ui';

export interface HarnessReport {
  id: string;
  created_at: string;
  report: {
    window_days: number;
    event_count: number;
    findings: { kind: string; count: number; proposal: string; examples: string[] }[];
    regression_candidates: Record<string, unknown>[];
  };
}

export function HarnessReports({ reports }: { reports: HarnessReport[] | null }) {
  if (!reports?.length) return <p className="muted">No reports yet.</p>;
  return (
    <div className="harness-reports">
      {reports.map((row, index) => (
        <details className="report" key={row.id} open={index === 0}>
          <summary>
            <span>{formatDate(row.created_at)}</span>
            <span className="subtle">
              {row.report.event_count} friction events · {row.report.window_days} days
            </span>
          </summary>
          <div className="harness-report-body">
            {row.report.findings.length ? (
              row.report.findings.map((finding) => (
                <article className="harness-finding" key={finding.kind}>
                  <div className="finding-heading">
                    <h3>{finding.kind.replaceAll('_', ' ')}</h3>
                    <span className="subtle">
                      {finding.count} occurrence{finding.count === 1 ? '' : 's'}
                    </span>
                  </div>
                  <p>{finding.proposal}</p>
                  {finding.examples.filter(Boolean).length ? (
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
            {row.report.regression_candidates.length ? (
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
