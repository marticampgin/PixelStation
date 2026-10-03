import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { PokerView } from '../src/features/games/PokerView';

it('clamps a raise after bots change its legal range in the same turn and retains valid manual amounts', async () => {
  let phase = 0;
  const minimums = [20, 60, 90, 90];
  const maximums = [1000, 1000, 1000, 100];
  const state = () => ({
    id: 'range-table',
    hand_number: 1,
    stage: 'preflop',
    actor: 0,
    board: [],
    pot: 30,
    dealer: 0,
    seats: [
      {
        index: 0,
        name: 'You',
        stack: 1000,
        bet: 0,
        contribution: 0,
        folded: false,
        all_in: false,
        hole: ['As', 'Kh'],
        personality: 'human',
      },
    ],
    legal_actions: [
      { action: 'call', amount: 10 },
      { action: 'raise', min: minimums[phase], max: maximums[phase] },
    ],
    history: [],
    winners: [],
    completed: false,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/actions?')) phase += 1;
      return new Response(JSON.stringify(state()), {
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
  const user = userEvent.setup();
  render(<PokerView />);
  await user.click(screen.getByRole('button', { name: /Poker No-Limit/ }));
  await user.click(screen.getByRole('button', { name: 'Start table' }));
  expect(await screen.findByRole('spinbutton', { name: 'Raise amount' })).toHaveValue(20);
  await user.click(screen.getByRole('button', { name: 'Call 10' }));
  await waitFor(() =>
    expect(screen.getByRole('spinbutton', { name: 'Raise amount' })).toHaveValue(60),
  );
  expect(screen.getByRole('button', { name: 'Raise' })).toBeEnabled();

  await user.clear(screen.getByRole('spinbutton', { name: 'Raise amount' }));
  await user.type(screen.getByRole('spinbutton', { name: 'Raise amount' }), '60.5');
  expect(screen.getByRole('button', { name: 'Raise' })).toBeDisabled();
  await user.clear(screen.getByRole('spinbutton', { name: 'Raise amount' }));
  await user.type(screen.getByRole('spinbutton', { name: 'Raise amount' }), '60');
  expect(screen.getByRole('button', { name: 'Raise' })).toBeEnabled();

  await user.clear(screen.getByRole('spinbutton', { name: 'Raise amount' }));
  await user.type(screen.getByRole('spinbutton', { name: 'Raise amount' }), '120');
  await user.click(screen.getByRole('button', { name: 'Call 10' }));
  await waitFor(() =>
    expect(screen.getByRole('spinbutton', { name: 'Raise amount' })).toHaveAttribute('min', '90'),
  );
  expect(screen.getByRole('spinbutton', { name: 'Raise amount' })).toHaveValue(120);

  await user.click(screen.getByRole('button', { name: 'Call 10' }));
  await waitFor(() =>
    expect(screen.getByRole('spinbutton', { name: 'Raise amount' })).toHaveValue(100),
  );
  expect(screen.getByRole('button', { name: 'Raise' })).toBeEnabled();
});

it.each([
  { label: 'all opponents exhausted', stacks: [3000, 0, 0], canAdvance: false },
  { label: 'human exhausted', stacks: [0, 2000, 1000], canAdvance: false },
  { label: 'two funded players remaining', stacks: [1000, 1000, 0], canAdvance: true },
])(
  'offers another hand only when the table can continue: $label',
  async ({ stacks, canAdvance }) => {
    const state = {
      id: 'completed-table',
      hand_number: 2,
      stage: 'showdown',
      actor: null,
      board: [],
      pot: 0,
      dealer: 0,
      seats: stacks.map((stack, index) => ({
        index,
        name: index === 0 ? 'You' : `AI ${index}`,
        stack,
        bet: 0,
        contribution: 0,
        folded: false,
        all_in: false,
        hole: ['As', 'Kh'],
        personality: index === 0 ? 'human' : 'balanced',
      })),
      legal_actions: [],
      history: [],
      winners: [],
      completed: true,
    };
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify(state), { headers: { 'Content-Type': 'application/json' } }),
    );
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    render(<PokerView />);
    await user.click(screen.getByRole('button', { name: /Poker No-Limit/ }));
    await user.click(screen.getByRole('button', { name: 'Start table' }));
    await screen.findAllByText('Hand complete');
    if (canAdvance) {
      expect(screen.getByRole('button', { name: 'Next hand' })).toBeEnabled();
      await user.click(screen.getByRole('button', { name: 'Next hand' }));
      await waitFor(() =>
        expect(fetcher).toHaveBeenCalledWith(
          '/api/poker/sessions/completed-table/next-hand?progressive=true&expected_sequence=0',
          expect.objectContaining({ method: 'POST' }),
        ),
      );
      expect(
        screen.queryByText('Table complete. Use New table to play again.'),
      ).not.toBeInTheDocument();
    } else {
      expect(screen.queryByRole('button', { name: 'Next hand' })).not.toBeInTheDocument();
      expect(screen.getByText('Table complete. Use New table to play again.')).toBeInTheDocument();
      expect(screen.getAllByRole('button', { name: 'New table' })).toHaveLength(1);
    }
  },
);
