import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { HarnessPanel } from '../src/features/settings/HarnessPanel';
import type { Evaluation, Watchtower } from '../src/features/settings/harnessTypes';
import type { Settings } from '../src/types';

const settings: Settings = {
  roles: { primary_chat: 'fixture:model' },
  ollama_url: 'http://127.0.0.1:11434',
  context_tokens: 8192,
  auto_memory: true,
  retrieval_count: 4,
  searxng_url: '',
  comfyui_url: '',
  critic_enabled: false,
  max_steps: 6,
  keep_alive: '0',
  summary_turns: 8,
  profile: 'lite',
  harness_enabled: true,
  harness_interval_hours: 24,
};
const distribution = { sample_count: 3, median: 200, p95: 500 };
const summary: Watchtower = {
  reports: [],
  runs: [],
  events: [],
  defaults: { retrieval_count: { default: 4, reason: 'Conservative, not empirically tuned.' } },
  metrics: {
    window_days: 7,
    sample_count: 3,
    matching_runs: 3,
    capped: false,
    instrumented_runs: 3,
    terminal_runs: 3,
    outcomes: { complete: 1, error: 1, interrupted: 1 },
    completion_rate: 1 / 3,
    repair_attempts: 1,
    fallbacks: 1,
    latency_ms: distribution,
    first_public_token_ms: { sample_count: 0, median: null, p95: null },
    groups: [],
    budgets: [],
    problem_counts: {},
    recent_problem_runs: [],
    native_usage: {
      prompt_token_samples: 0,
      generation_token_samples: 0,
      prompt_tokens: 0,
      generated_tokens: 0,
      tokens_per_second: { sample_count: 0, median: null, p95: null },
      note: 'Missing metadata is not zero usage.',
    },
    interpretation: 'Workflow completion is not answer correctness.',
  },
};
const evaluation: Evaluation = {
  id: 'eval-one',
  created_at: '2026-10-03T10:00:00Z',
  report: {
    kind: 'evaluation',
    status: 'RUNNING',
    fixture: { version: 'fixture-v1', sha256: 'hash' },
  },
};
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('local watchtower', () => {
  it('shows sample denominators, unavailable measurements and explicit private export', async () => {
    const fetcher = vi.fn(async () => json(summary));
    vi.stubGlobal('fetch', fetcher);
    render(<HarnessPanel settings={settings} onSettings={vi.fn()} />);
    await screen.findByText('Recorded runs · 7 days');
    expect(screen.getByText('33%')).toBeInTheDocument();
    expect(screen.getByText('1 of 3 terminal runs')).toBeInTheDocument();
    expect(screen.getByText('500 ms')).toBeInTheDocument();
    expect(screen.getByText('3 samples · median 200 ms')).toBeInTheDocument();
    expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0);
    const link = screen.getByRole('link', { name: 'Download diagnostics JSON' });
    expect(link).toHaveAttribute('href', '/api/harness/diagnostics');
    await userEvent.click(screen.getByLabelText('Include bounded private local details'));
    expect(link).toHaveAttribute('href', '/api/harness/diagnostics?include_content=true');
    expect(screen.getByText(/Review the file before sharing/)).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('persists and polls an explicit evaluation while preventing duplicate starts', async () => {
    let finished = false;
    let polls = 0;
    const complete: Evaluation = {
      ...evaluation,
      report: {
        ...evaluation.report,
        status: 'COMPLETE',
        outcome: 'PASS',
        counts: { PASS: 7, SKIP: 2 },
        cases: [
          {
            id: 'native_plan',
            label: 'Native plan',
            scope: 'native_model',
            status: 'SKIP',
            reason: 'Native probe was not requested.',
          },
        ],
      },
    };
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/harness/evaluations') {
        expect(JSON.parse(String(init?.body))).toEqual({ native: false });
        return json(evaluation, 202);
      }
      if (url.endsWith('/evaluations/eval-one')) {
        if (++polls === 1) return json({ detail: 'Temporary connection failure.' }, 503);
        finished = true;
        return json(complete);
      }
      return json({ ...summary, reports: finished ? [complete] : [] });
    });
    vi.stubGlobal('fetch', fetcher);
    render(<HarnessPanel settings={settings} onSettings={vi.fn()} />);
    await screen.findByText('Recorded runs · 7 days');
    await userEvent.click(screen.getByRole('button', { name: 'Run evaluations' }));
    expect(screen.getByRole('button', { name: 'Evaluation running…' })).toBeDisabled();
    await screen.findByText('COMPLETE · PASS', {}, { timeout: 3000 });
    expect(screen.getByText('SKIP 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run evaluations' })).toBeEnabled();
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/harness/evaluations')).toHaveLength(
      1,
    );
    expect(polls).toBe(2);
  });

  it('only requests native inference after a manual opt-in and click, and reports rejection', async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/harness/evaluations') {
        expect(JSON.parse(String(init?.body))).toEqual({ native: true });
        return json({ detail: 'Local inference is busy.' }, 409);
      }
      return json(summary);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<HarnessPanel settings={settings} onSettings={vi.fn()} />);
    await screen.findByText('Recorded runs · 7 days');
    await userEvent.click(screen.getByLabelText(/Include native model probes/));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: 'Run evaluations' }));
    await screen.findByText('Local inference is busy.');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Run evaluations' })).toBeEnabled(),
    );
  });

  it('persists independent animated groups and exposes successful-case repair evidence', async () => {
    const complete: Evaluation = {
      ...evaluation,
      report: {
        ...evaluation.report,
        status: 'COMPLETE',
        runner_version: 2,
        outcome: 'PASS',
        counts: { PASS: 1 },
        cases: [
          {
            id: 'native_plan',
            label: 'Native plan',
            scope: 'native_model',
            duration_ms: 220,
            status: 'PASS',
            measurements: {
              completion_path: 'production_answer_stream',
              validation_rejections: 1,
              repair_attempts: 1,
            },
          },
        ],
      },
    };
    const fetcher = vi.fn(async (_url: string) => json({ ...summary, reports: [complete] }));
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    const content = <HarnessPanel settings={settings} onSettings={vi.fn()} />;
    const mounted = render(content);
    await screen.findByText(/fixture-v1 · runner 2/);
    const latency = screen.getByRole('button', { name: 'Latency by route and model' });
    const budget = screen.getByRole('button', { name: 'Native token measurements and budget use' });
    expect(latency).toHaveAttribute('aria-expanded', 'false');
    expect(budget).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    await user.click(latency);
    expect(latency).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(budget).toHaveAttribute('aria-expanded', 'false');
    const gate = screen.getByRole('button', { name: 'PASS Native plan 220 ms' });
    const region = document.getElementById(gate.getAttribute('aria-controls')!)!;
    expect(region).toHaveAttribute('inert');
    gate.focus();
    await user.keyboard('{Enter}');
    expect(gate).toHaveFocus();
    expect(gate).toHaveAttribute('aria-expanded', 'true');
    expect(region).not.toHaveAttribute('inert');
    expect(region).toHaveTextContent('"completion_path": "production_answer_stream"');
    expect(region).toHaveTextContent('"validation_rejections": 1');
    expect(region).toHaveTextContent('"repair_attempts": 1');
    mounted.unmount();
    render(content);
    await screen.findByText('COMPLETE · PASS');
    expect(screen.getByRole('button', { name: 'Latency by route and model' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(
      screen.getByRole('button', { name: 'Native token measurements and budget use' }),
    ).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: 'PASS Native plan 220 ms' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(fetcher.mock.calls.every(([url]) => url === '/api/harness')).toBe(true);
  });

  it('keeps global chat limits out of Poker run budgets', async () => {
    const configuration = { retrieval_count: 4, max_steps: 6, bounded_response_tokens: 2048 };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        json({
          ...summary,
          metrics: {
            ...summary.metrics,
            budgets: [
              { id: 'poker-run', route: 'poker_bot', configuration },
              {
                id: 'chat-run',
                route: 'file_qa',
                configuration,
                retrieved_memories: 2,
                tool_steps: 3,
                public_output_tokens_estimated: 31,
              },
            ],
          },
        }),
      ),
    );
    render(<HarnessPanel settings={settings} onSettings={vi.fn()} />);
    await screen.findByText('Recorded runs · 7 days');
    await userEvent.click(
      screen.getByRole('button', { name: 'Native token measurements and budget use' }),
    );
    const poker = screen.getByRole('row', { name: /^poker_bot/ });
    const cells = within(poker).getAllByRole('cell');
    expect(cells[1]).toHaveTextContent('Not recorded');
    expect(cells[2]).toHaveTextContent('Not applicable');
    expect(cells[3]).toHaveTextContent('Generation limit not recorded');
    expect(cells[4]).toHaveTextContent('Not applicable');
    expect(poker).not.toHaveTextContent('2048');
    expect(poker).not.toHaveTextContent('/ 4');
    expect(poker).not.toHaveTextContent('Research ceiling');
    const chat = screen.getByRole('row', { name: /^file_qa/ });
    expect(chat).toHaveTextContent('2 / 4');
    expect(chat).toHaveTextContent('Configured chat answer ceiling 2048');
    expect(chat).toHaveTextContent('Research ceiling 6');
    expect(screen.getByText(/Native usage is aggregated above/)).toBeInTheDocument();
  });
});
