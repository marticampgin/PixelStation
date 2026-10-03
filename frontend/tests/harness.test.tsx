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
        expect(JSON.parse(String(init?.body))).toEqual({ native: false, poker_native: false });
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
        expect(JSON.parse(String(init?.body))).toEqual({ native: true, poker_native: false });
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

  it.each([
    {
      generationLimit: 180,
      expected: 'Recorded per-attempt generation ceiling 180 tokens',
    },
    {
      generationLimit: undefined,
      expected: 'Per-attempt generation ceiling not recorded',
    },
  ])(
    'keeps chat limits out of Poker budgets with recorded ceiling $generationLimit',
    async ({ generationLimit, expected }) => {
      const configuration = { retrieval_count: 4, max_steps: 6, bounded_response_tokens: 2048 };
      vi.stubGlobal(
        'fetch',
        vi.fn(async () =>
          json({
            ...summary,
            metrics: {
              ...summary.metrics,
              budgets: [
                {
                  id: 'poker-run',
                  route: 'poker_bot',
                  configuration,
                  generation_tokens_per_attempt: generationLimit,
                },
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
      expect(cells[3]).toHaveTextContent(expected);
      expect(cells[3]).not.toHaveTextContent(/ceiling 0 tokens/);
      if (generationLimit === undefined) expect(cells[3]).not.toHaveTextContent('180');
      expect(cells[4]).toHaveTextContent('Not applicable');
      expect(poker).not.toHaveTextContent('2048');
      expect(poker).not.toHaveTextContent('/ 4');
      expect(poker).not.toHaveTextContent('Research ceiling');
      const chat = screen.getByRole('row', { name: /^file_qa/ });
      expect(chat).toHaveTextContent('2 / 4');
      expect(chat).toHaveTextContent('Configured chat answer ceiling 2048');
      expect(chat).toHaveTextContent('Research ceiling 6');
      expect(screen.getByText(/Native usage is aggregated above/)).toBeInTheDocument();
    },
  );

  it.each([
    { native: false, poker_native: true, scopes: 'Poker strategy' },
    { native: true, poker_native: true, scopes: 'chat model and Poker strategy' },
  ])('only starts manually selected scopes: $scopes', async ({ native, poker_native, scopes }) => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/harness/evaluations') {
        expect(JSON.parse(String(init?.body))).toEqual({ native, poker_native });
        return json(
          {
            ...evaluation,
            report: {
              ...evaluation.report,
              native_requested: native,
              poker_native_requested: poker_native,
            },
          },
          202,
        );
      }
      return json(summary);
    });
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    render(<HarnessPanel settings={settings} onSettings={vi.fn()} />);
    await screen.findByText('Recorded runs · 7 days');
    const chatOption = screen.getByRole('checkbox', { name: /Include native model probes/ });
    const pokerOption = screen.getByRole('checkbox', { name: /Include Poker strategy probes/ });
    expect(chatOption).not.toBeChecked();
    expect(pokerOption).not.toBeChecked();
    expect(screen.getByText(/up to 180 seconds of local inference/)).toBeInTheDocument();
    await user.click(pokerOption);
    if (native) await user.click(chatOption);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Run evaluations' }));
    await screen.findByText(`Requested native scopes: ${scopes}.`);
    expect(screen.getByRole('status')).toHaveTextContent(`explicitly requested ${scopes} probes`);
    expect(chatOption).toBeDisabled();
    expect(pokerOption).toBeDisabled();
  });

  it('restores skipped Poker cases without selecting or starting native probes', async () => {
    const skipped: Evaluation = {
      ...evaluation,
      report: {
        ...evaluation.report,
        status: 'COMPLETE',
        outcome: 'PASS',
        native_requested: false,
        poker_native_requested: false,
        counts: { PASS: 9, SKIP: 6 },
        cases: [
          {
            id: 'poker_decision_fixture',
            label: 'Poker decision fixture',
            scope: 'native_poker',
            status: 'SKIP',
            reason: 'Poker strategy probes were not requested.',
          },
        ],
      },
    };
    const fetcher = vi.fn(async () => json({ ...summary, reports: [skipped] }));
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    render(<HarnessPanel settings={settings} onSettings={vi.fn()} />);
    await screen.findByText('SKIP 6');
    expect(
      screen.getByRole('checkbox', { name: /Include Poker strategy probes/ }),
    ).not.toBeChecked();
    expect(screen.getByText('Requested native scopes: none.')).toBeInTheDocument();
    const caseToggle = screen.getByRole('button', {
      name: 'SKIP Poker decision fixture native_poker',
    });
    await user.click(caseToggle);
    const region = document.getElementById(caseToggle.getAttribute('aria-controls')!)!;
    expect(region).toHaveAttribute('aria-hidden', 'false');
    expect(region).toHaveTextContent('Poker strategy probes were not requested.');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('distinguishes rejected raw all-in attempts, selected actions, and fallback decisions', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        json({
          ...summary,
          metrics: {
            ...summary.metrics,
            poker_strategy: {
              sample_count: 3,
              actions: { fold: 2, call: 1 },
              native_without_fallback: 2,
              fallback_decisions: 1,
              preflop_samples: 3,
              preflop_all_ins: 0,
              raw_preflop_all_in_attempts: 2,
              validation_rejections: 2,
              note: 'Observed behavior does not measure Poker skill.',
            },
          },
        }),
      ),
    );
    render(<HarnessPanel settings={settings} onSettings={vi.fn()} />);
    await screen.findByText('Recorded runs · 7 days');
    await userEvent.click(
      screen.getByRole('button', { name: 'Native token measurements and budget use' }),
    );
    const observations = within(screen.getByRole('group', { name: 'Production Poker decisions' }));
    expect(observations.getByText('Measured decisions:', { exact: false })).toHaveTextContent(
      'Measured decisions: 3',
    );
    expect(observations.getByText('Native without fallback:', { exact: false })).toHaveTextContent(
      'Native without fallback: 2',
    );
    expect(observations.getByText('Fallback decisions:', { exact: false })).toHaveTextContent(
      'Fallback decisions: 1',
    );
    expect(observations.getByText('Final selected actions:', { exact: false })).toHaveTextContent(
      'fold: 2 · call: 1',
    );
    expect(observations.getByText('Selected preflop all-ins:', { exact: false })).toHaveTextContent(
      'Selected preflop all-ins: 0 across 3 preflop decisions. Raw model preflop all-in attempts: 2.',
    );
    expect(observations.getByText('Validation rejections: 2.')).toBeInTheDocument();
    expect(observations.getByText(/does not measure Poker skill/)).toBeInTheDocument();
    expect(
      observations.getByText(/Synthetic strategy scenarios are reported separately/),
    ).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });

  it.each([
    undefined,
    {
      sample_count: 0,
      actions: {},
      native_without_fallback: 0,
      fallback_decisions: 0,
      preflop_samples: 0,
      preflop_all_ins: 0,
      raw_preflop_all_in_attempts: 0,
      validation_rejections: 0,
      note: 'No measured samples.',
    },
  ])(
    'shows unavailable Poker observations when no decisions are measured',
    async (poker_strategy) => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => json({ ...summary, metrics: { ...summary.metrics, poker_strategy } })),
      );
      render(<HarnessPanel settings={settings} onSettings={vi.fn()} />);
      await screen.findByText('Recorded runs · 7 days');
      await userEvent.click(
        screen.getByRole('button', { name: 'Native token measurements and budget use' }),
      );
      const observations = screen.getByRole('group', { name: 'Production Poker decisions' });
      expect(observations).toHaveTextContent(
        'Poker observations unavailable: no measured decisions.',
      );
      expect(observations).not.toHaveTextContent('Native without fallback: 0');
      expect(observations).not.toHaveTextContent('0%');
    },
  );
});
