import { CalendarDays, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { errorMessage, post, remove, request } from '../../api/client';
import { EmptyState, ErrorNotice, Loading, Modal } from '../../components/ui';
import { useResource } from '../../hooks/useResource';
import type { Approval, CalendarEvent, GoogleServiceStatus } from '../../types';
import { ApprovalCard } from '../google/ApprovalCard';
import { GoogleSetup } from '../google/GoogleSetup';

const loadStatus = () => request<GoogleServiceStatus>('/google/calendar/status');
const localDate = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const localDateTime = (value?: string) =>
  value ? `${localDate(new Date(value))}T${new Date(value).toTimeString().slice(0, 5)}` : '';
export function CalendarView() {
  const status = useResource(loadStatus);
  const [date, setDate] = useState(localDate(new Date()));
  const [query, setQuery] = useState('');
  const [calendarId, setCalendarId] = useState('primary');
  const [calendars, setCalendars] = useState<{ id: string; summary: string }[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [selected, setSelected] = useState<CalendarEvent | null>(null);
  const [editing, setEditing] = useState(false);
  const [summary, setSummary] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [description, setDescription] = useState('');
  const [location, setLocation] = useState('');
  const [allDay, setAllDay] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [approval, setApproval] = useState<Approval | null>(null);
  async function action(run: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await run();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function loadEvents() {
    await action(async () => {
      const min = new Date(`${date}T00:00:00`);
      const max = new Date(min);
      max.setDate(max.getDate() + 14);
      const result = await request<{ items: CalendarEvent[] }>(
        `/google/calendar/events?calendar_id=${encodeURIComponent(calendarId)}&time_min=${encodeURIComponent(min.toISOString())}&time_max=${encodeURIComponent(max.toISOString())}&q=${encodeURIComponent(query)}`,
      );
      setEvents(result.items);
    });
  }
  useEffect(() => {
    if (status.data?.connected)
      void request<{ items: { id: string; summary: string }[] }>('/google/calendar/calendars')
        .then((result) => setCalendars(result.items))
        .catch((err) => setError(errorMessage(err)));
  }, [status.data?.connected]);
  useEffect(() => {
    if (status.data?.connected) void loadEvents();
  }, [date, calendarId, status.data?.connected]);
  function edit(event: CalendarEvent | null) {
    setSelected(event);
    setSummary(event?.summary ?? '');
    setAllDay(Boolean(event?.start.date));
    setStart(event?.start.date ?? (event ? localDateTime(event.start.dateTime) : `${date}T09:00`));
    setEnd(event?.end.date ?? (event ? localDateTime(event.end.dateTime) : `${date}T10:00`));
    setDescription(event?.description ?? '');
    setLocation(event?.location ?? '');
    setEditing(true);
  }
  function changeAllDay(value: boolean) {
    setAllDay(value);
    if (value) {
      const day = start.slice(0, 10);
      const next = new Date(`${day}T12:00:00`);
      next.setDate(next.getDate() + 1);
      setStart(day);
      setEnd(end.slice(0, 10) > day ? end.slice(0, 10) : localDate(next));
    } else {
      setStart(`${start}T09:00`);
      setEnd(`${end}T10:00`);
    }
  }
  async function propose() {
    await action(async () => {
      if (new Date(end) <= new Date(start)) throw new Error('The event must end after it starts.');
      const event = {
        summary,
        start: allDay
          ? { date: start }
          : {
              dateTime: new Date(start).toISOString(),
              timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            },
        end: allDay
          ? { date: end }
          : {
              dateTime: new Date(end).toISOString(),
              timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            },
        description,
        location,
      };
      const result = selected
        ? await request<{ approval: Approval }>(`/google/calendar/events/${selected.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ calendar_id: calendarId, event }),
          })
        : await post<{ approval: Approval }>('/google/calendar/events', {
            calendar_id: calendarId,
            event,
          });
      setApproval(result.approval);
      setEditing(false);
    });
  }
  const conflicts = events.filter(
    (event) =>
      event.id !== selected?.id &&
      new Date(event.start.dateTime ?? event.start.date ?? '') < new Date(end) &&
      new Date(event.end.dateTime ?? event.end.date ?? '') > new Date(start),
  );
  if (status.loading)
    return (
      <div className="feature-page">
        <Loading text="Checking Calendar connection…" />
      </div>
    );
  if (!status.data?.connected)
    return (
      <div className="feature-page">
        <div className="page-heading">
          <h1>Calendar</h1>
        </div>
        <ErrorNotice message={status.error} />
        <GoogleSetup service="calendar" onConnected={status.setData} />
      </div>
    );
  return (
    <div className="feature-page">
      <div className="page-heading">
        <div>
          <h1>Calendar</h1>
          <p>Agenda for the next two weeks.</p>
          {status.data.account ? (
            <p>{status.data.account.calendar_id || status.data.account.label}</p>
          ) : null}
        </div>
        <button className="button" onClick={() => edit(null)}>
          <Plus size={16} />
          Create event
        </button>
      </div>
      <ErrorNotice message={error} />
      <div className="toolbar">
        <input
          type="date"
          aria-label="Agenda start date"
          value={date}
          onChange={(event) => setDate(event.target.value)}
        />
        <button className="button secondary" onClick={() => setDate(localDate(new Date()))}>
          Today
        </button>
        <select
          aria-label="Calendar"
          value={calendarId}
          onChange={(event) => setCalendarId(event.target.value)}
        >
          <option value="primary">Primary calendar</option>
          {calendars
            .filter((calendar) => calendar.id !== 'primary')
            .map((calendar) => (
              <option value={calendar.id} key={calendar.id}>
                {calendar.summary}
              </option>
            ))}
        </select>
      </div>
      <form
        className="web-search"
        onSubmit={(event) => {
          event.preventDefault();
          void loadEvents();
        }}
      >
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Search calendar events"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search events"
          />
        </label>
        <button className="button secondary" type="submit">
          Search
        </button>
      </form>
      {approval ? <ApprovalCard approval={approval} onComplete={() => void loadEvents()} /> : null}
      {busy ? (
        <Loading text="Reading calendar…" />
      ) : events.length ? (
        <div className="agenda">
          {events.map((event) => (
            <article className="agenda-event" key={event.id}>
              <CalendarDays size={20} className="accent" />
              <div className="agenda-content">
                <h3>{event.summary || 'Untitled event'}</h3>
                <p className="subtle">
                  {new Date(
                    event.start.dateTime ?? `${event.start.date}T00:00:00`,
                  ).toLocaleString()}{' '}
                  — {new Date(event.end.dateTime ?? `${event.end.date}T00:00:00`).toLocaleString()}
                </p>
                {event.location ? <p className="subtle">{event.location}</p> : null}
                {event.description ? <p>{event.description}</p> : null}
              </div>
              <div className="row-actions">
                <button
                  className="icon-button"
                  title="Edit event"
                  aria-label="Edit event"
                  onClick={() => edit(event)}
                >
                  <Pencil size={16} />
                </button>
                <button
                  className="icon-button"
                  title="Delete event"
                  aria-label="Delete event"
                  onClick={() =>
                    void action(async () => {
                      const result = await remove<{ approval: Approval }>(
                        `/google/calendar/events/${event.id}?calendar_id=${encodeURIComponent(calendarId)}`,
                      );
                      setApproval(result.approval);
                    })
                  }
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState title="No events in this period">
          Choose another date or create an event.
        </EmptyState>
      )}
      {editing ? (
        <Modal title={selected ? 'Edit event' : 'Create event'} onClose={() => setEditing(false)}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void propose();
            }}
          >
            <label>
              Title
              <input
                required
                value={summary}
                onChange={(event) => setSummary(event.target.value)}
              />
            </label>
            <label className="toggle-field">
              <input
                type="checkbox"
                checked={allDay}
                onChange={(event) => changeAllDay(event.target.checked)}
              />
              All day
            </label>
            <div className="form-grid">
              <label>
                Start
                <input
                  type={allDay ? 'date' : 'datetime-local'}
                  required
                  value={start}
                  onChange={(event) => setStart(event.target.value)}
                />
              </label>
              <label>
                {allDay ? 'End date (exclusive)' : 'End'}
                <input
                  type={allDay ? 'date' : 'datetime-local'}
                  required
                  value={end}
                  onChange={(event) => setEnd(event.target.value)}
                />
              </label>
            </div>
            <label>
              Location
              <input value={location} onChange={(event) => setLocation(event.target.value)} />
            </label>
            <label>
              Description
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={3}
              />
            </label>
            {conflicts.length ? (
              <div className="notice">
                Overlaps with: {conflicts.map((event) => event.summary).join(', ')}
              </div>
            ) : null}
            <ErrorNotice message={error} />
            <div className="modal-actions">
              <button className="button" type="submit" disabled={busy}>
                Review changes
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </div>
  );
}
