package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/event"
)

// HandleEvents registers GET /api/v1/events, the event stream of log as
// server-sent events. A request without Last-Event-ID receives the events
// appended from then on; one with it, the kept events after that seq
// first. Every stream ends when ctx is done: the daemon cancels it before
// it shuts the server down, because the shutdown waits for open streams.
func (s *Server) HandleEvents(ctx context.Context, log *event.Log) {
	s.Handle("GET /api/v1/events", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		streamEvents(ctx, log, w, r)
	}))
}

func streamEvents(stop context.Context, log *event.Log, w http.ResponseWriter, r *http.Request) {
	sub, ok := subscribe(log, w, r)
	if !ok {
		return
	}
	defer sub.Close()

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)
	rc := http.NewResponseController(w)
	defer cutWritesOn(rc, stop.Done(), sub.Done())()
	if rc.Flush() != nil {
		return
	}
	for _, ev := range sub.Replay {
		if writeEvent(w, rc, ev) != nil {
			return
		}
	}
	for {
		select {
		case <-stop.Done():
			return
		case <-r.Context().Done():
			return
		case ev, ok := <-sub.C:
			if !ok {
				// Dropped for falling behind; the client resumes with its
				// cursor.
				return
			}
			if writeEvent(w, rc, ev) != nil {
				return
			}
		}
	}
}

// subscribe subscribes to log from the request's Last-Event-ID, or from now
// when it has none. When it cannot, it answers the refusal and reports
// false.
func subscribe(log *event.Log, w http.ResponseWriter, r *http.Request) (*event.Subscription, bool) {
	// An absent header and an empty one differ: only Values tells them
	// apart.
	values := r.Header.Values("Last-Event-ID")
	if len(values) == 0 {
		return log.SubscribeNow(), true
	}
	seq, ok := parseCursor(values[0])
	if !ok {
		WriteError(w, http.StatusBadRequest, "invalid_request", fmt.Sprintf("Last-Event-ID %q is not a seq", values[0]))
		return nil, false
	}
	sub, err := log.Subscribe(r.Context(), seq)
	var expired *event.CursorExpiredError
	switch {
	case err == nil:
		return sub, true
	case errors.Is(err, event.ErrInvalidCursor):
		WriteError(w, http.StatusBadRequest, "invalid_request", fmt.Sprintf("Last-Event-ID %d is greater than the newest seq", seq))
	case errors.As(err, &expired):
		WriteErrorWith(w, http.StatusGone, "cursor_expired",
			fmt.Sprintf("events after %d are no longer kept; the oldest kept is %d", seq, expired.OldestSeq),
			map[string]any{"oldest_seq": expired.OldestSeq})
	default:
		WriteError(w, http.StatusInternalServerError, "internal", "the event log cannot be read")
	}
	return nil, false
}

// parseCursor accepts a non-negative decimal integer written with digits
// only: no sign, no space, no fraction.
func parseCursor(v string) (int64, bool) {
	if v == "" {
		return 0, false
	}
	for _, c := range v {
		if c < '0' || c > '9' {
			return 0, false
		}
	}
	seq, err := strconv.ParseInt(v, 10, 64)
	return seq, err == nil
}

// cutWritesOn makes a write to a client that does not read fail at once
// when stop or dropped is closed: such a write blocks for as long as the
// client does not read, and would otherwise hold the stream open after its
// subscriber was dropped, and the daemon's shutdown with it. The returned
// function ends the watch; it is called before the handler returns, after
// which the response must not be touched.
func cutWritesOn(rc *http.ResponseController, stop, dropped <-chan struct{}) (end func()) {
	var mu sync.Mutex
	ended := false
	done := make(chan struct{})
	go func() {
		select {
		case <-stop:
		case <-dropped:
		case <-done:
			return
		}
		mu.Lock()
		defer mu.Unlock()
		if !ended {
			rc.SetWriteDeadline(time.Now())
		}
	}()
	return func() {
		mu.Lock()
		ended = true
		mu.Unlock()
		close(done)
	}
}

// writeEvent writes ev as one server-sent event and flushes it.
func writeEvent(w io.Writer, rc *http.ResponseController, ev event.Event) error {
	data, err := json.Marshal(ev)
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "id: %d\nevent: %s\ndata: %s\n\n", ev.Seq, ev.Type, data); err != nil {
		return err
	}
	return rc.Flush()
}
