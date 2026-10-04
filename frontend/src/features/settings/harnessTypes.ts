import type { HarnessReport } from './HarnessReports';

export interface Distribution {
  sample_count: number;
  median: number | null;
  p95: number | null;
  min?: number | null;
  max?: number | null;
}
export interface RecordedRun {
  id: string;
  route: string;
  model: string | null;
  status: string;
  latency_ms: number;
  started_at: string;
  metrics: { repair_count?: number; fallback_count?: number };
}
export interface EvaluationCase {
  id: string;
  label: string;
  status: 'PASS' | 'FAIL' | 'SKIP' | 'ERROR';
  scope: string;
  duration_ms?: number;
  reason?: string;
  error_type?: string;
  measurements?: Record<string, unknown>;
}
export interface Evaluation {
  id: string;
  created_at: string;
  report: {
    kind: string;
    status: string;
    runner_version?: number;
    outcome?: string;
    native_requested?: boolean;
    poker_native_requested?: boolean;
    fixture?: { version: string; sha256: string };
    counts?: Record<string, number>;
    cases?: EvaluationCase[];
    baseline_id?: string | null;
    comparison?: {
      id: string;
      previous_status: string;
      status: string;
      regressed: boolean;
      duration_delta_ms: number;
    }[];
  };
}
export interface Watchtower {
  reports: (Evaluation | HarnessReport)[];
  runs: RecordedRun[];
  events: { id: string; kind: string; created_at: string }[];
  defaults: Record<string, { default: number; reason: string }>;
  metrics: {
    window_days: number;
    sample_count: number;
    matching_runs: number;
    capped: boolean;
    instrumented_runs: number;
    terminal_runs: number;
    outcomes: Record<string, number>;
    completion_rate: number | null;
    repair_attempts: number;
    fallbacks: number;
    latency_ms: Distribution;
    first_public_token_ms: Distribution;
    groups: {
      route: string;
      model: string | null;
      sample_count: number;
      latency_ms: Distribution;
      outcomes: Record<string, number>;
    }[];
    native_usage: {
      prompt_token_samples: number;
      generation_token_samples: number;
      prompt_tokens: number;
      generated_tokens: number;
      tokens_per_second: Distribution;
      note: string;
    };
    poker_strategy?: {
      sample_count: number;
      actions: Record<string, number>;
      native_without_fallback: number;
      fallback_decisions: number;
      preflop_samples: number;
      preflop_all_ins: number;
      raw_preflop_all_in_attempts: number;
      validation_rejections: number;
      note: string;
    };
    problem_counts: Record<string, number>;
    recent_problem_runs: RecordedRun[];
    interpretation: string;
    budgets: {
      id: string;
      route: string;
      retrieved_memories?: number;
      retrieved_chunks?: number;
      public_output_tokens_estimated?: number;
      generation_tokens_per_attempt?: number;
      tool_steps?: number | null;
      configuration?: {
        retrieval_count: number;
        max_steps: number;
        bounded_response_tokens: number;
      };
      context_tokens_estimated?: { total: number; budget: number };
    }[];
  };
}
