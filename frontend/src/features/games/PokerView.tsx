import { ArrowLeft, Gamepad2, Plus } from 'lucide-react';
import { useEffect, useState } from 'react';
import { errorMessage, post, request } from '../../api/client';
import { ErrorNotice, Loading } from '../../components/ui';
import { useLocalStorage } from '../../hooks/useLocalStorage';
import type { PokerState } from '../../types';

const suits: Record<string, string> = { s: '♠', h: '♥', d: '♦', c: '♣' };
export function PlayingCard({ card }: { card?: string }) {
  const suit = card?.slice(-1).toLowerCase();
  return (
    <div
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
  const [state, setState] = useState<PokerState | null>(null);
  const [seats, setSeats] = useState(3);
  const [stack, setStack] = useState(1000);
  const [amount, setAmount] = useState(20);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (sessionId)
      request<PokerState>(`/poker/sessions/${sessionId}`)
        .then(setState)
        .catch((err) => {
          if (err.status === 404) setSessionId(null);
          else setError(errorMessage(err));
        });
  }, [sessionId]);
  useEffect(() => {
    const legalRaise = state?.legal_actions.find((action) => action.action === 'raise');
    if (legalRaise?.min) setAmount(legalRaise.min);
  }, [state?.hand_number, state?.actor, state?.stage]);
  async function action(run: () => Promise<PokerState>) {
    setBusy(true);
    setError('');
    try {
      const next = await run();
      setState(next);
      setSessionId(next.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  const start = () =>
    action(() =>
      post<PokerState>('/poker/sessions', { seats, stack, small_blind: 5, big_blind: 10 }),
    );
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
            <span className="subtle">2–6 seats · Local AI opponents</span>
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
            setSessionId(null);
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
            Blinds: 5 / 10. Cards, legal actions, and chip accounting are controlled by the rules
            engine.
          </p>
          <button className="button" type="submit" disabled={busy}>
            {busy ? 'Dealing…' : 'Start table'}
          </button>
        </form>
      ) : (
        <>
          <div className="poker-info">
            <span>Hand {state.hand_number}</span>
            <span>{state.stage}</span>
            <span>Blinds 5 / 10</span>
          </div>
          <div className="poker-table">
            <div className="poker-seats">
              {state.seats
                .filter((seat) => seat.index !== 0)
                .map((seat) => (
                  <div
                    className={`poker-seat ${state.actor === seat.index ? 'acting' : ''} ${seat.folded ? 'folded' : ''}`}
                    key={seat.index}
                  >
                    <strong>
                      {seat.name}
                      {state.dealer === seat.index ? <span className="dealer">D</span> : null}
                    </strong>
                    <div className="hole-cards">
                      {(seat.hole?.length ? seat.hole : [undefined, undefined]).map((card, i) => (
                        <PlayingCard card={card} key={i} />
                      ))}
                    </div>
                    <span>{seat.stack} chips</span>
                    <small>
                      {seat.folded
                        ? 'Folded'
                        : seat.all_in
                          ? 'All-in'
                          : seat.bet
                            ? `Bet ${seat.bet}`
                            : seat.personality}
                    </small>
                  </div>
                ))}
            </div>
            <div className="poker-board">
              <div className="board-cards">
                {Array.from({ length: 5 }, (_, i) => (
                  <PlayingCard key={i} card={state.board[i]} />
                ))}
              </div>
              <div className="pot">
                Pot <strong>{state.pot}</strong>
              </div>
            </div>
            <div className="human-seat">
              <strong>You{state.dealer === 0 ? <span className="dealer">D</span> : null}</strong>
              <div className="hole-cards">
                {state.seats[0]?.hole.map((card, i) => (
                  <PlayingCard card={card} key={i} />
                ))}
              </div>
              <span>
                {state.seats[0]?.stack} chips · Bet {state.seats[0]?.bet}
              </span>
            </div>
          </div>
          {busy ? (
            <Loading text="AI opponents are choosing their actions…" />
          ) : (
            <div className="poker-actions">
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
                      disabled={amount < (legal.min ?? 0) || amount > (legal.max ?? Infinity)}
                      onClick={() =>
                        void action(() =>
                          post<PokerState>(`/poker/sessions/${state.id}/actions`, {
                            action: 'raise',
                            amount,
                          }),
                        )
                      }
                    >
                      Raise
                    </button>
                  </div>
                ) : (
                  <button
                    className={`button ${legal.action === 'fold' ? 'secondary' : ''}`}
                    key={legal.action}
                    onClick={() =>
                      void action(() =>
                        post<PokerState>(`/poker/sessions/${state.id}/actions`, {
                          action: legal.action,
                        }),
                      )
                    }
                  >
                    {legal.action
                      .replace('_', '-')
                      .replace(/^./, (character) => character.toUpperCase())}
                    {legal.amount ? ` ${legal.amount}` : ''}
                  </button>
                ),
              )}
              {state.completed ? (
                <button
                  className="button"
                  onClick={() =>
                    void action(() => post<PokerState>(`/poker/sessions/${state.id}/next-hand`, {}))
                  }
                >
                  Next hand
                </button>
              ) : null}
            </div>
          )}
          {state.completed ? (
            <div className="poker-result">
              {state.winners.length
                ? `Hand complete · ${state.winners.map((winner) => (state.seats[winner.seat]?.name ?? 'Player') + ' receives ' + winner.amount + ' chips (' + winner.hand + ')').join(' · ')}`
                : 'Hand complete'}
            </div>
          ) : null}
          {state.model_status ? (
            <p className="subtle poker-model-status">{state.model_status}</p>
          ) : null}
          <details className="poker-history">
            <summary>Action history</summary>
            {state.history.map((entry, i) => (
              <div key={i}>
                <span>{entry.stage}</span> {state.seats[entry.seat]?.name ?? `Seat ${entry.seat}`}:{' '}
                {entry.action}
                {entry.amount ? ` ${entry.amount}` : ''}
              </div>
            ))}
          </details>
        </>
      )}
    </div>
  );
}
