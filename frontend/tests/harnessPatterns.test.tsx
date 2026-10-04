import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { HarnessReports, type HarnessReport } from '../src/features/settings/HarnessReports';

const report: HarnessReport = {
  id: 'report1',
  created_at: '2026-10-04T08:00:00Z',
  report: {
    kind: 'passive',
    window_days: 7,
    event_count: 3,
    regression_candidates: [],
    interpretation: 'Repeated observations do not establish root cause or answer correctness.',
    patterns: [
      {
        id: 'pattern1',
        kind: 'response_error',
        route: 'web_research',
        model: 'fixture:model',
        status: 'error',
        count: 3,
        run_count: 2,
        run_ids: ['a'.repeat(32), 'b'.repeat(32)],
        error_codes: ['search_engines_unavailable'],
        error_types: ['IntegrationError'],
        failed_tools: ['web_search'],
        stages: ['tool_execution'],
        validations: [],
        classification: 'structured_observations',
        recommendation:
          'Run an actual SearXNG JSON search and distinguish upstream failure from empty results.',
        regression_hint: 'Test forbidden, partial and empty responses separately.',
        links_capped: false,
      },
    ],
  },
};

describe('passive failure pattern reports', () => {
  it('shows repeat counts and recorded context with working local run links and a checkable regression hint', async () => {
    render(<HarnessReports reports={[report]} />);
    expect(screen.getByText('3 events · 2 linked runs')).toBeVisible();
    expect(screen.getByText('web_research · fixture:model · error')).toBeVisible();
    expect(screen.getByText('search_engines_unavailable')).toBeVisible();
    expect(screen.getByText('web_search')).toBeVisible();
    expect(screen.getByText(/do not establish root cause/)).toBeVisible();
    await userEvent.click(screen.getByText('Regression candidate'));
    expect(
      screen.getByText('Test forbidden, partial and empty responses separately.'),
    ).toBeVisible();
    await userEvent.click(screen.getByText('Inspect linked local runs'));
    expect(screen.getByRole('link', { name: 'Run aaaaaaaa' })).toHaveAttribute(
      'href',
      `/api/harness/runs/${'a'.repeat(32)}`,
    );
    expect(screen.queryByText('Private examples')).not.toBeInTheDocument();
  });

  it('renders legacy redacted findings without requiring private example fields', () => {
    render(
      <HarnessReports
        reports={[
          {
            ...report,
            report: {
              window_days: 7,
              event_count: 1,
              findings: [
                { kind: 'manual_problem', count: 1, proposal: 'Review the recorded workflow.' },
              ],
              regression_candidates: [],
            },
          },
        ]}
      />,
    );
    expect(screen.getByText('Review the recorded workflow.')).toBeVisible();
    expect(screen.queryByText('Examples')).not.toBeInTheDocument();
  });

  it('labels missing structured evidence honestly and explains bounded links', () => {
    render(
      <HarnessReports
        reports={[
          {
            ...report,
            report: {
              ...report.report,
              patterns: [
                {
                  ...report.report.patterns![0],
                  route: null,
                  model: null,
                  status: null,
                  error_codes: [],
                  error_types: [],
                  failed_tools: [],
                  stages: [],
                  classification: 'unclassified',
                  links_capped: true,
                },
              ],
            },
          },
        ]}
      />,
    );
    expect(screen.getByText('Unclassified')).toBeVisible();
    expect(
      screen.getByText('Route unavailable · Model unavailable · Status unavailable'),
    ).toBeVisible();
    expect(
      screen.getByText('At most 20 run and event links are included for this pattern.'),
    ).toBeInTheDocument();
  });
});
