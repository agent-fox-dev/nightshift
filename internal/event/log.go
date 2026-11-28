// Package event is the daemon's event log: every change a client of the
// event stream sees is appended here with a seq one greater than the last,
// written to the store before any subscriber receives it, and kept for
// seven days.
package event

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/store"
)

// Buffer is how many events wait for one subscriber before it is dropped:
// an append never waits for a reader, and a reader that reconnects with its
// cursor loses nothing.
const Buffer = 256

// Retention is how long an event is kept.
const Retention = 7 * 24 * time.Hour

func init() {
	// The counter holds the highest seq ever given, so that seq never goes
	// back once the event that carried it is pruned.
	store.Register("event",
		`CREATE TABLE events (
			seq INTEGER PRIMARY KEY,
			time INTEGER NOT NULL,
			type TEXT NOT NULL,
			data TEXT NOT NULL
		)`,
		`CREATE INDEX events_time ON events (time)`,
		`CREATE TABLE event_counter (
			id INTEGER PRIMARY KEY CHECK (id = 1),
			last_seq INTEGER NOT NULL
		)`,
		`INSERT INTO event_counter (id, last_seq) VALUES (1, 0)`,
	)
}

// Event is one entry of the log.
type Event struct {
	Seq  int64           `json:"seq"`
	Time time.Time       `json:"time"`
	Type string          `json:"type"`
	Data json.RawMessage `json:"data"`
}

// ErrInvalidCursor is the error for a cursor that is negative or greater
// than the newest seq ever given.
var ErrInvalidCursor = errors.New("the cursor is not a seq the log has given")

// CursorExpiredError is the error for a cursor after which an event was
// removed from the log.
type CursorExpiredError struct {
	// OldestSeq is the seq of the oldest kept event, or the next seq to be
	// given when none is kept.
	OldestSeq int64
}

func (e *CursorExpiredError) Error() string {
	return fmt.Sprintf("events before seq %d are no longer kept", e.OldestSeq)
}

// Log is the event log over a store. Its methods are safe for concurrent
// use; one Log is opened per store.
type Log struct {
	db    *sql.DB
	clock func() time.Time

	// mu orders appends and guards last and subs, so that seqs are given
	// without a gap and a subscriber registered under it misses nothing.
	mu   sync.Mutex
	last int64
	subs map[*Subscription]chan Event
}

// Open returns the log kept in st, whose events take their time from clock.
func Open(st *store.Store, clock func() time.Time) (*Log, error) {
	l := &Log{db: st.DB(), clock: clock, subs: map[*Subscription]chan Event{}}
	if err := l.db.QueryRow(`SELECT last_seq FROM event_counter WHERE id = 1`).Scan(&l.last); err != nil {
		return nil, fmt.Errorf("event log: %w", err)
	}
	return l, nil
}

// Append adds an event of type typ with data as its JSON, writes it to the
// store, and only then hands it to every subscriber. A subscriber whose
// buffer is full is dropped rather than waited for.
func (l *Log) Append(ctx context.Context, typ string, data any) (Event, error) {
	raw, err := json.Marshal(data)
	if err != nil {
		return Event{}, fmt.Errorf("event %s: %w", typ, err)
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	// Through UnixNano and back, so that the event returned equals the one
	// read from the store.
	ev := Event{Seq: l.last + 1, Time: time.Unix(0, l.clock().UnixNano()).UTC(), Type: typ, Data: raw}
	if err := l.write(ctx, ev); err != nil {
		return Event{}, fmt.Errorf("event %s: %w", typ, err)
	}
	l.last = ev.Seq
	for sub, ch := range l.subs {
		select {
		case ch <- ev:
		default:
			delete(l.subs, sub)
			close(ch)
			close(sub.done)
		}
	}
	return ev, nil
}

func (l *Log) write(ctx context.Context, ev Event) error {
	tx, err := l.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `INSERT INTO events (seq, time, type, data) VALUES (?, ?, ?, ?)`,
		ev.Seq, ev.Time.UnixNano(), ev.Type, string(ev.Data)); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE event_counter SET last_seq = ? WHERE id = 1`, ev.Seq); err != nil {
		return err
	}
	return tx.Commit()
}

// After returns the kept events with a seq greater than seq, in order. It
// fails with ErrInvalidCursor when seq is negative or greater than the
// newest seq ever given, and with a *CursorExpiredError when an event after
// seq was removed.
func (l *Log) After(ctx context.Context, seq int64) ([]Event, error) {
	return l.read(ctx, seq, -1)
}

// read returns the kept events in (after, upto], or up to the newest when
// upto is negative, checking the cursor against the same snapshot of the
// store as the events it reads, so that a prune in between cannot leave a
// gap.
func (l *Log) read(ctx context.Context, after, upto int64) ([]Event, error) {
	tx, err := l.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var last, oldest int64
	if err := tx.QueryRowContext(ctx, `SELECT last_seq, COALESCE((SELECT MIN(seq) FROM events), last_seq + 1) FROM event_counter WHERE id = 1`).Scan(&last, &oldest); err != nil {
		return nil, err
	}
	if after < 0 || after > last {
		return nil, ErrInvalidCursor
	}
	if after+1 < oldest {
		return nil, &CursorExpiredError{OldestSeq: oldest}
	}
	if upto < 0 {
		upto = last
	}
	rows, err := tx.QueryContext(ctx, `SELECT seq, time, type, data FROM events WHERE seq > ? AND seq <= ? ORDER BY seq`, after, upto)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := []Event{}
	for rows.Next() {
		var ev Event
		var nanos int64
		var data string
		if err := rows.Scan(&ev.Seq, &nanos, &ev.Type, &data); err != nil {
			return nil, err
		}
		ev.Time, ev.Data = time.Unix(0, nanos).UTC(), json.RawMessage(data)
		events = append(events, ev)
	}
	return events, rows.Err()
}

// Newest is the highest seq ever given, 0 before the first event.
func (l *Log) Newest() int64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.last
}

// Subscription delivers the events of the log to one reader: first Replay,
// then C.
type Subscription struct {
	// Replay holds the kept events after the subscription's cursor that
	// were appended before it began, in order.
	Replay []Event
	// C carries every event appended after Replay's last; it is closed when
	// the subscription is closed or dropped because Buffer events waited.
	C <-chan Event

	log *Log
	// done is closed with C, when the subscription is closed or dropped.
	done chan struct{}
}

// Done is closed when the subscription is closed or dropped. Unlike C, it
// is closed at once, before the events still waiting in C are read, so that
// a reader blocked on a slow client can give up.
func (s *Subscription) Done() <-chan struct{} { return s.done }

// Subscribe returns a subscription to the events after afterSeq: those
// kept in its Replay, and those appended from now on in its C, none missed
// and none twice. It fails as After does for a cursor that is invalid or
// expired.
func (l *Log) Subscribe(ctx context.Context, afterSeq int64) (*Subscription, error) {
	l.mu.Lock()
	if afterSeq < 0 || afterSeq > l.last {
		l.mu.Unlock()
		return nil, ErrInvalidCursor
	}
	sub := l.subscribe()
	newest := l.last
	l.mu.Unlock()

	// The replay is read outside the lock, so that a long one does not hold
	// up appends; what is appended meanwhile waits in C.
	replay, err := l.read(ctx, afterSeq, newest)
	if err != nil {
		sub.Close()
		return nil, err
	}
	sub.Replay = replay
	return sub, nil
}

// SubscribeNow returns a subscription to the events appended from now on.
func (l *Log) SubscribeNow() *Subscription {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.subscribe()
}

// subscribe registers a subscription; l.mu is held.
func (l *Log) subscribe() *Subscription {
	ch := make(chan Event, Buffer)
	sub := &Subscription{C: ch, log: l, done: make(chan struct{})}
	l.subs[sub] = ch
	return sub
}

// Close ends the subscription and closes C, unless it was dropped already.
func (s *Subscription) Close() {
	l := s.log
	l.mu.Lock()
	defer l.mu.Unlock()
	if ch, ok := l.subs[s]; ok {
		delete(l.subs, s)
		close(ch)
		close(s.done)
	}
}

// Subscribers is the number of open subscriptions.
func (l *Log) Subscribers() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.subs)
}

// Prune removes every event older than Retention by the log's clock. The
// highest seq ever given stays, so seq goes on from it.
func (l *Log) Prune(ctx context.Context) error {
	cutoff := l.clock().Add(-Retention).UnixNano()
	if _, err := l.db.ExecContext(ctx, `DELETE FROM events WHERE time < ?`, cutoff); err != nil {
		return fmt.Errorf("event log: prune: %w", err)
	}
	return nil
}
