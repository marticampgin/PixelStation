import { ArrowLeft, Clock3, Gamepad2, Plus, RotateCcw } from 'lucide-react';
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { ApiError, errorMessage, post, request } from '../../api/client';
import { ErrorNotice } from '../../components/ui';
import { useLocalStorage } from '../../hooks/useLocalStorage';
import type { PokerState } from '../../types';
import './poker.css';

const suits: Record<string, string> = { s: '♠', h: '♥', d: '♦', c: '♣' };
export interface PokerLogEntry {
  sequence: number;
  created_at: string;
  hand_number: number;
  stage: string;
  seat: number | null;
  action: string;
  amount: number;
  pot: number;
}
export type LivePokerState = PokerState & {
  event_sequence?: number;
  event_log?: PokerLogEntry[];
  needs_step?: boolean;
  phase?: string;
  small_blind?: number;
  big_blind?: number;
};
type PokerStreamEvent = { type: string; phase?: string; state?: LivePokerState; error?: string };
const actionName = (action: string) =>
  action.replaceAll('_', '-').replace(/^./, (character) => character.toUpperCase());
const needsStep = (table: LivePokerState) =>
  table.needs_step ?? (!table.completed && table.actor !== 0);

function waitForPace(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException('Viewing paused', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, milliseconds);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

/** Await every event before requesting another committed opponent action. */
export async function readPokerStep(
  response: Response,
  onEvent: (event: PokerStreamEvent) => Promise<void>,
) {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(response.status, payload?.detail ?? response.statusText);
  }
  if (!response.body) throw new Error('The table returned an empty turn stream.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      const lines = pending.split('\n');
      pending = lines.pop() ?? '';
      for (const line of lines) if (line.trim()) await onEvent(JSON.parse(line));
      if (done) {
        if (pending.trim()) await onEvent(JSON.parse(pending));
        break;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export function seatPosition(index: number, count: number) {
  const positions: Record<number, [number, number][]> = {
    2: [
      [50, 87],
      [50, 12],
    ],
    3: [
      [50, 87],
      [19, 20],
      [81, 20],
    ],
    4: [
      [50, 87],
      [15, 37],
      [50, 12],
      [85, 37],
    ],
    5: [
      [50, 87],
      [13, 67],
      [22, 17],
      [78, 17],
      [87, 67],
    ],
    6: [
      [50, 87],
      [13, 67],
      [18, 18],
      [50, 10],
      [82, 18],
      [87, 67],
    ],
  };
  const [x, y] = (positions[count] ?? positions[6])[index] ?? [50, 50];
  return { '--seat-x': `${x}%`, '--seat-y': `${y}%` } as CSSProperties;
}

export function PlayingCard({ card }: { card?: string }) {
  const suit = card?.slice(-1).toLowerCase();
  return (
    <div
      aria-label={
        card ? `${card.slice(0, -1).replace('T', '10')}${suits[suit!] ?? suit}` : 'Hidden card'
      }
      className={`playing-card ${card ? '' : 'back'} ${suit === 'h' || suit === 'd' ? 'red' : ''}`}
    >
      {card ? (
        <>
          <span>{card.slice(0, -1).replace('T', '10')}</span>
          <span>{suits[suit!] ?? suit}</span>
        </>
      ) : (
        <div className="card-pixels" />
      )}
    </div>
  );
}

export function PokerView() {
  const [open, setOpen] = useState(false);
  const [sessionId, setSessionId] = useLocalStorage<string | null>('poker-session', null);
  const [pace, setPace] = useLocalStorage<number>('poker-pace', 1500);
  const paceRef = useRef(pace);
  paceRef.current = Math.min(2000, Math.max(1000, pace));
  const [state, setState] = useState<LivePokerState | null>(null);
  const stateRef = useRef<LivePokerState | null>(null);
  const [seats, setSeats] = useState(3);
  const [stack, setStack] = useState(1000);
  const [amount, setAmount] = useState(20);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const operationRef = useRef(0);
  const [error, setError] = useState('');
  const [phase, setPhase] = useState('ready');
  const [flight, setFlight] = useState<PokerLogEntry | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const legalRaise = state?.legal_actions.find((action) => action.action === 'raise');
  const raiseMin = legalRaise?.min;
  const raiseMax = legalRaise?.max;
  const tableComplete = Boolean(
    state?.completed &&
    (!state.seats.some((seat) => seat.index === 0 && seat.stack > 0) ||
      state.seats.filter((seat) => seat.stack > 0).length < 2),
  );
  const logs = state?.event_log ?? [];
  const latest = logs.at(-1);
  const actor = state?.seats.find((seat) => seat.index === state.actor);
  const turnLabel = state?.completed
    ? 'Hand complete'
    : phase === 'choosing'
      ? `${actor?.name ?? 'Opponent'} is choosing`
      : phase === 'queued'
        ? `${actor?.name ?? 'Opponent'} · waiting for local model`
        : state?.actor === 0 && !busy && !needsStep(state)
          ? 'Your turn'
          : actor
            ? `Up next · ${actor.name}`
            : 'Dealing the next street';

  function display(next: LivePokerState) {
    const entry = next.event_log?.at(-1);
    setFlight(
      entry &&
        entry.amount > 0 &&
        entry.seat !== null &&
        entry.sequence !== stateRef.current?.event_sequence
        ? entry
        : null,
    );
    stateRef.current = next;
    setState(next);
    setPhase(next.phase ?? 'ready');
  }
  async function drive(initial: LivePokerState, signal: AbortSignal) {
    let current = initial;
    for (let step = 0; needsStep(current); step += 1) {
      if (step >= 128)
        throw new Error('This opponent phase reached its action limit. Resume the saved table.');
      const response = await fetch(`/api/poker/sessions/${current.id}/steps`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_sequence: current.event_sequence ?? 0 }),
        signal,
      });
      let committed = false;
      await readPokerStep(response, async (event) => {
        if (signal.aborted) throw new DOMException('Viewing paused', 'AbortError');
        if (event.type === 'error') throw new Error(event.error);
        if (event.state) {
          display(event.state);
          if (event.type === 'state') {
            current = event.state;
            committed = true;
            await waitForPace(paceRef.current, signal);
          }
        }
      });
      if (!committed)
        throw new Error('The turn stream ended before an action was saved. Resume the table.');
    }
    setPhase(current.phase ?? 'ready');
  }
  async function perform(
    run: (signal: AbortSignal) => Promise<LivePokerState>,
    pauseFirst = false,
  ) {
    if (busyRef.current) return;
    const token = ++operationRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      const next = await run(controller.signal);
      if (controller.signal.aborted) return;
      display(next);
      setSessionId(next.id);
      if (pauseFirst && needsStep(next)) await waitForPace(paceRef.current, controller.signal);
      await drive(next, controller.signal);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err));
    } finally {
      if (operationRef.current === token) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }
  useEffect(() => {
    if (open && sessionId)
      void perform((signal) => request<LivePokerState>(`/poker/sessions/${sessionId}`, { signal }));
    return () => {
      controllerRef.current?.abort();
      operationRef.current += 1;
      busyRef.current = false;
      setBusy(false);
    };
    // Opening resumes the saved actor. Changing the ID after create must not duplicate its stream.
  }, [open]);
  useEffect(() => {
    if (raiseMin !== undefined)
      setAmount((previous) => Math.min(raiseMax ?? Infinity, Math.max(raiseMin, previous)));
  }, [raiseMin, raiseMax]);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [latest?.sequence]);
  const start = () =>
    perform(
      (signal) =>
        post<LivePokerState>(
          '/poker/sessions?progressive=true',
          { seats, stack, small_blind: 5, big_blind: 10 },
          signal,
        ),
      true,
    );
  const act = (action: string, raise?: number) => {
    if (!state || busyRef.current) return;
    void perform(
      (signal) =>
        post<LivePokerState>(
          `/poker/sessions/${state.id}/actions?progressive=true`,
          { action, amount: raise, expected_sequence: state.event_sequence ?? 0 },
          signal,
        ),
      true,
    );
  };
  if (!open)
    return (
      <div className="feature-page">
        <div className="page-heading">
          <h1>Games</h1>
        </div>
        <ErrorNotice message={error} />
        <button className="game-card" onClick={() => setOpen(true)}>
          <div className="game-art">
            <PlayingCard card="As" />
            <PlayingCard card="Kh" />
          </div>
          <div>
            <h2>Poker</h2>
            <p>No-Limit Texas Hold'em</p>
            <span className="subtle">
              2–6 seats · Local AI opponents{sessionId ? ' · Resume your table' : ''}
            </span>
          </div>
          <Gamepad2 size={22} />
        </button>
      </div>
    );
  return (
    <div className="feature-page poker-page">
      <div className="page-heading">
        <div className="row-actions">
          <button className="icon-button" aria-label="Back to Games" onClick={() => setOpen(false)}>
            <ArrowLeft size={20} />
          </button>
          <h1>Poker</h1>
        </div>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => {
            setState(null);
            stateRef.current = null;
            setFlight(null);
            setSessionId(null);
            setError('');
          }}
        >
          <Plus size={15} />
          New table
        </button>
      </div>
      <ErrorNotice message={error} />
      {!state ? (
        <form
          className="poker-setup detail-block"
          onSubmit={(event) => {
            event.preventDefault();
            void start();
          }}
        >
          <h2>Set up a table</h2>
          <div className="form-grid">
            <label>
              Seats
              <select
                aria-label="Poker seats"
                value={seats}
                onChange={(event) => setSeats(Number(event.target.value))}
              >
                {[2, 3, 4, 5, 6].map((count) => (
                  <option value={count} key={count}>
                    {count} players
                  </option>
                ))}
              </select>
            </label>
            <label>
              Starting chips
              <input
                aria-label="Starting chips"
                type="number"
                min="100"
                max="100000"
                step="100"
                value={stack}
                onChange={(event) => setStack(Number(event.target.value))}
              />
            </label>
          </div>
          <p className="subtle">
            Blinds: 5 / 10. Choose your move when the table highlights your seat.
          </p>
          <button className="button" type="submit" disabled={busy}>
            {busy ? 'Dealing…' : 'Start table'}
          </button>
        </form>
      ) : (
        <>
          <div className="poker-toolbar">
            <div className="poker-info">
              <span>Hand {state.hand_number}</span>
              <span className="poker-street">{state.stage}</span>
              <span>
                Blinds {state.small_blind ?? 5} / {state.big_blind ?? 10}
              </span>
            </div>
            <label className="poker-pace">
              <Clock3 size={14} />
              Action pace
              <select
                aria-label="Action pace"
                value={pace}
                onChange={(event) => setPace(Number(event.target.value))}
              >
                <option value={1000}>1 second</option>
                <option value={1500}>1.5 seconds</option>
                <option value={2000}>2 seconds</option>
              </select>
            </label>
          </div>
          <div
            className={`poker-turn-banner ${state.actor === 0 && !busy ? 'your-turn' : ''}`}
            role="status"
            aria-live="polite"
          >
            <span
              className={
                phase === 'choosing' || phase === 'queued' ? 'poker-thinking-dot' : 'poker-turn-dot'
              }
            />
            <strong>{turnLabel}</strong>
            <span>
              {latest?.seat !== null && latest?.seat !== undefined
                ? `${state.seats[latest.seat]?.name ?? 'Player'} · ${actionName(latest.action)}${latest.amount ? ` ${latest.amount}` : ''}`
                : latest?.action === 'deal'
                  ? `${actionName(latest.stage)} cards dealt`
                  : 'Follow the highlighted seat'}
            </span>
          </div>
          <div
            className="poker-table poker-live-table"
            aria-label="Poker table"
            data-seats={state.seats.length}
          >
            <div className="poker-felt-mark" aria-hidden="true">
              PIXEL STATION
            </div>
            {state.seats.map((seat) => (
              <div
                style={seatPosition(seat.index, state.seats.length)}
                className={`poker-seat poker-live-seat ${state.actor === seat.index && !state.completed ? 'acting' : ''} ${seat.folded ? 'folded' : ''} ${seat.index === 0 ? 'is-human' : ''}`}
                key={seat.index}
                data-seat={seat.index}
                aria-current={state.actor === seat.index && !state.completed ? 'true' : undefined}
              >
                <div className="poker-avatar-wrap">
                  <img
                    className="poker-avatar"
                    src={`/assets/poker/seat-${seat.index}.png`}
                    alt=""
                  />
                  {state.dealer === seat.index ? (
                    <span className="dealer" aria-label="Dealer">
                      D
                    </span>
                  ) : null}
                  {state.actor === seat.index && !state.completed ? (
                    <span className="poker-seat-turn">
                      {phase === 'choosing'
                        ? 'Choosing…'
                        : phase === 'queued'
                          ? 'Queued'
                          : seat.index === 0 && !busy
                            ? 'Your turn'
                            : 'Up next'}
                    </span>
                  ) : null}
                </div>
                <strong>{seat.name}</strong>
                <div className="hole-cards">
                  {(seat.hole?.length ? seat.hole : [undefined, undefined]).map((card, i) => (
                    <PlayingCard
                      card={card}
                      key={`${state.hand_number}-${i}-${card ?? 'hidden'}`}
                    />
                  ))}
                </div>
                <span className="poker-stack">{seat.stack.toLocaleString()} chips</span>
                <small>
                  {seat.folded
                    ? 'Folded'
                    : seat.all_in
                      ? 'All-in'
                      : seat.bet
                        ? `Bet ${seat.bet}`
                        : seat.index === 0
                          ? 'You'
                          : seat.personality}
                </small>
              </div>
            ))}
            <div className="poker-board">
              <div className="board-cards">
                {Array.from({ length: 5 }, (_, i) => (
                  <PlayingCard
                    key={`${state.hand_number}-${i}-${state.board[i] ?? 'hidden'}`}
                    card={state.board[i]}
                  />
                ))}
              </div>
              <div className="pot">
                <span className="poker-pot-chips" aria-hidden="true">
                  ● ● ●
                </span>
                {state.completed ? 'Awarded pot' : 'Pot'}{' '}
                <strong>{state.pot.toLocaleString()}</strong>
              </div>
            </div>
            {flight?.seat !== null && flight?.seat !== undefined ? (
              <div
                key={flight.sequence}
                className="poker-chip-flight"
                data-testid="chip-flight"
                data-seat={flight.seat}
                style={seatPosition(flight.seat, state.seats.length)}
                aria-hidden="true"
              >
                <i />
                <i />
                <i />
                <b>+{flight.amount}</b>
              </div>
            ) : null}
          </div>
          <fieldset
            className="poker-actions poker-live-actions"
            disabled={busy || needsStep(state)}
            aria-label="Your poker actions"
          >
            {state.legal_actions.map((legal) =>
              legal.action === 'raise' ? (
                <div className="raise-control" key="raise">
                  <label>
                    Raise to
                    <input
                      aria-label="Raise amount"
                      type="number"
                      min={legal.min}
                      max={legal.max}
                      value={amount}
                      onChange={(event) => setAmount(Number(event.target.value))}
                    />
                  </label>
                  <button
                    className="button"
                    disabled={
                      !Number.isFinite(amount) ||
                      !Number.isInteger(amount) ||
                      amount < (legal.min ?? 0) ||
                      amount > (legal.max ?? Infinity)
                    }
                    onClick={() => act('raise', amount)}
                  >
                    Raise
                  </button>
                </div>
              ) : (
                <button
                  className={`button ${legal.action === 'fold' ? 'secondary' : ''}`}
                  key={legal.action}
                  onClick={() => act(legal.action)}
                >
                  {actionName(legal.action)}
                  {legal.amount ? ` ${legal.amount}` : ''}
                </button>
              ),
            )}
            {state.completed && !tableComplete ? (
              <button
                className="button"
                onClick={() =>
                  void perform(
                    (signal) =>
                      post<LivePokerState>(
                        `/poker/sessions/${state.id}/next-hand?progressive=true&expected_sequence=${state.event_sequence ?? 0}`,
                        {},
                        signal,
                      ),
                    true,
                  )
                }
              >
                Next hand
              </button>
            ) : null}
            {busy ? (
              <span className="poker-control-note">
                Watching the opponent phase · leaving pauses the next move
              </span>
            ) : null}
          </fieldset>
          {error && needsStep(state) && !busy ? (
            <button
              className="button secondary"
              onClick={() =>
                void perform((signal) =>
                  request<LivePokerState>(`/poker/sessions/${state.id}`, { signal }),
                )
              }
            >
              <RotateCcw size={15} />
              Resume saved turn
            </button>
          ) : null}
          {state.completed ? (
            <div className="poker-result">
              {state.winners.length
                ? `Hand complete · ${state.winners.map((winner) => (state.seats[winner.seat]?.name ?? 'Player') + ' receives ' + winner.amount + ' chips (' + winner.hand + ')').join(' · ')}`
                : 'Hand complete'}
            </div>
          ) : null}
          {tableComplete ? (
            <p className="subtle">Table complete. Use New table to play again.</p>
          ) : null}
          {state.model_status ? (
            <p className="subtle poker-model-status">{state.model_status}</p>
          ) : null}
          <section className="poker-action-log" aria-label="Action log">
            <div className="poker-log-heading">
              <h2>Action log</h2>
              <span>Moves appear after they are saved</span>
            </div>
            <div className="poker-log-scroll" ref={logRef}>
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Street</th>
                    <th>Player / action</th>
                    <th>Chips</th>
                    <th>Pot</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.length
                    ? logs.slice(-100).map((entry) => (
                        <tr
                          key={entry.sequence}
                          className={entry.sequence === latest?.sequence ? 'latest' : ''}
                        >
                          <td>
                            <time dateTime={entry.created_at}>
                              {new Date(entry.created_at).toLocaleTimeString([], {
                                hour: '2-digit',
                                minute: '2-digit',
                                second: '2-digit',
                              })}
                            </time>
                          </td>
                          <td>
                            <span className="poker-log-street">
                              H{entry.hand_number} · {entry.stage}
                            </span>
                          </td>
                          <td>
                            {entry.seat === null
                              ? actionName(
                                  entry.action === 'deal' ? `${entry.stage} dealt` : entry.action,
                                )
                              : `${state.seats[entry.seat]?.name ?? `Seat ${entry.seat}`} · ${actionName(entry.action)}`}
                          </td>
                          <td>{entry.amount ? `+${entry.amount}` : '—'}</td>
                          <td>{entry.pot}</td>
                        </tr>
                      ))
                    : state.history.map((entry, i) => (
                        <tr key={i}>
                          <td>—</td>
                          <td>{entry.stage}</td>
                          <td>
                            {state.seats[entry.seat]?.name}: {actionName(entry.action)}
                          </td>
                          <td>{entry.amount || '—'}</td>
                          <td>—</td>
                        </tr>
                      ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
