import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import {
  PokerView,
  readPokerStep,
  seatPosition,
  type LivePokerState,
} from '../src/features/games/PokerView';

const snapshot = (sequence: number, actor: number, needs_step: boolean): LivePokerState => ({
  id: 'paced-table',
  hand_number: 1,
  stage: 'preflop',
  board: [],
  pot: sequence === 4 ? 30 : 20,
  actor,
  dealer: 0,
  event_sequence: sequence,
  needs_step,
  phase: needs_step ? 'waiting' : 'ready',
  seats: Array.from({ length: 6 }, (_, index) => ({
    index,
    name: index === 0 ? 'You' : `Pixel ${index}`,
    stack: 1000,
    bet: 0,
    contribution: 0,
    folded: false,
    all_in: false,
    hole: index === 0 ? ['As', 'Kh'] : [],
    personality: 'balanced',
  })),
  legal_actions:
    actor === 0
      ? [
          { action: 'call', amount: 10 },
          { action: 'raise', min: 20, max: 1000 },
        ]
      : [],
  history: [],
  completed: false,
  winners: [],
  event_log: [
    {
      sequence: 3,
      created_at: '2026-10-03T14:00:01+00:00',
      hand_number: 1,
      stage: 'preflop',
      seat: 0,
      action: 'call',
      amount: 5,
      pot: 20,
    },
    ...(sequence === 4
      ? [
          {
            sequence: 4,
            created_at: '2026-10-03T14:00:02+00:00',
            hand_number: 1,
            stage: 'preflop',
            seat: 1,
            action: 'raise',
            amount: 10,
            pot: 30,
          },
        ]
      : []),
  ],
});

it('shows actual choosing, pauses on navigation, resumes the saved actor, and animates committed chips', async () => {
  localStorage.setItem('pixel-station:v1:poker-pace', '1000');
  let saved = snapshot(2, 0, false);
  let stepCount = 0;
  let firstSignal: AbortSignal | undefined;
  let finish: (() => void) | undefined;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/actions?')) saved = snapshot(3, 1, true);
    if (url.endsWith('/steps')) {
      stepCount += 1;
      const encoder = new TextEncoder();
      return new Response(
        new ReadableStream({
          start(controller) {
            const send = (event: unknown) =>
              controller.enqueue(encoder.encode(JSON.stringify(event) + '\n'));
            send({ type: 'phase', state: { ...saved, phase: 'queued' } });
            send({ type: 'phase', state: { ...saved, phase: 'choosing' } });
            if (stepCount === 1) {
              firstSignal = init?.signal as AbortSignal;
              firstSignal.addEventListener(
                'abort',
                () => controller.error(new DOMException('Paused', 'AbortError')),
                { once: true },
              );
            } else {
              finish = () => {
                saved = snapshot(4, 0, false);
                send({ type: 'state', state: saved });
                send({ type: 'done' });
                controller.close();
              };
            }
          },
        }),
        { headers: { 'Content-Type': 'application/x-ndjson' } },
      );
    }
    return new Response(JSON.stringify(saved), { headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  render(<PokerView />);
  await user.click(screen.getByRole('button', { name: /Poker No-Limit/ }));
  await user.click(screen.getByRole('button', { name: 'Start table' }));
  expect(document.querySelectorAll('.poker-avatar')).toHaveLength(6);
  expect(
    Array.from(document.querySelectorAll('.poker-avatar')).map((image) =>
      image.getAttribute('src'),
    ),
  ).toEqual(Array.from({ length: 6 }, (_, index) => `/assets/poker/seat-${index}.png`));
  await user.click(screen.getByRole('button', { name: 'Call 10' }));
  expect(await screen.findByText('Pixel 1 is choosing', {}, { timeout: 2500 })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: 'Your poker actions' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'New table' })).toBeDisabled();
  expect(document.querySelectorAll('[aria-current="true"]')).toHaveLength(1);
  expect(document.querySelector('.poker-live-seat.acting')).toHaveTextContent('Pixel 1');
  expect(screen.queryByText('Hand complete')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Back to Games' }));
  expect(firstSignal?.aborted).toBe(true);
  await user.click(screen.getByRole('button', { name: /Poker No-Limit/ }));
  expect(await screen.findByText('Pixel 1 is choosing')).toBeInTheDocument();
  await act(async () => finish?.());
  expect(await screen.findByTestId('chip-flight')).toHaveAttribute('data-seat', '1');
  expect(screen.getByTestId('chip-flight')).toHaveTextContent('+10');
  const log = screen.getByRole('region', { name: 'Action log' });
  expect(within(log).getByText('Pixel 1 · Raise')).toBeInTheDocument();
  expect(log.querySelector('time[datetime="2026-10-03T14:00:02+00:00"]')).not.toBeNull();
  expect(within(log).getByText('30')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Call 10' })).toBeDisabled();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Call 10' })).toBeEnabled(), {
    timeout: 2500,
  });
  expect(stepCount).toBe(2);
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/actions?'))).toHaveLength(1);
  const bodies = fetcher.mock.calls
    .filter(([url]) => String(url).endsWith('/steps'))
    .map(([, init]) => JSON.parse(init?.body as string));
  expect(bodies).toEqual([{ expected_sequence: 3 }, { expected_sequence: 3 }]);
});

it('awaits asynchronous event handling and decodes UTF-8 split across network chunks', async () => {
  const bytes = new TextEncoder().encode('{"type":"phase","phase":"🦊"}\n{"type":"done"}\n');
  const chunks = [bytes.slice(0, 28), bytes.slice(28, 30), bytes.slice(30)];
  const response = new Response(
    new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(chunk));
        controller.close();
      },
    }),
  );
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const seen: string[] = [];
  const result = readPokerStep(response, async (event) => {
    seen.push(event.phase ?? event.type);
    if (event.type === 'phase') await gate;
  });
  await waitFor(() => expect(seen).toEqual(['🦊']));
  release?.();
  await result;
  expect(seen).toEqual(['🦊', 'done']);
});

it.each([2, 3, 4, 5, 6])(
  'assigns distinct table positions to every seat at a %i-player table',
  (count) => {
    const positions = Array.from({ length: count }, (_, index) => seatPosition(index, count));
    expect(new Set(positions.map((position) => JSON.stringify(position))).size).toBe(count);
  },
);
