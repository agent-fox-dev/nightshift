package api_test

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/api"
	"github.com/agent-fox-dev/nightshift/internal/event"
	"github.com/agent-fox-dev/nightshift/internal/store/storetest"
)

// frameTimeout bounds how long a test waits for the next event of a stream.
const frameTimeout = 2 * time.Second

var day0 = time.Date(2026, 11, 1, 12, 0, 0, 0, time.UTC)

// testClock is a time the test moves by hand.
type testClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *testClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *testClock) Set(t time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = t
}

// eventServer is an API server with the event stream of a real log over a
// store of the test's own.
type eventServer struct {
	api   *api.Server
	url   string
	log   *event.Log
	clock *testClock
}

func newEventServer(t *testing.T) *eventServer {
	t.Helper()
	c := &testClock{now: day0}
	l, err := event.Open(storetest.Open(t), c.Now)
	if err != nil {
		t.Fatalf("event.Open: %v", err)
	}
	s := api.New(clientToken, reachableHub())
	ctx, stop := context.WithCancel(context.Background())
	s.HandleEvents(ctx, l)
	srv := httptest.NewServer(s)
	// Cleanups run last first: the streams end before Close waits for them.
	t.Cleanup(srv.Close)
	t.Cleanup(stop)
	return &eventServer{api: s, url: srv.URL, log: l, clock: c}
}

func (e *eventServer) append(t *testing.T, n int, typ string) {
	t.Helper()
	for range n {
		if _, err := e.log.Append(context.Background(), typ, map[string]string{"k": "v"}); err != nil {
			t.Errorf("Append: %v", err)
			return
		}
	}
}

// frame is one event of a server-sent event stream.
type frame struct {
	id, event, data string
}

// stream is an open response of GET /api/v1/events.
type stream struct {
	resp   *http.Response
	frames chan frame
}

// openStream requests the event stream, with Last-Event-ID set to cursor
// unless it is nil, and reads its frames in the background.
func (e *eventServer) openStream(t *testing.T, cursor *string) *stream {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, e.url+"/api/v1/events", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+clientToken)
	if cursor != nil {
		req.Header.Set("Last-Event-ID", *cursor)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET /api/v1/events: %v", err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	s := &stream{resp: resp, frames: make(chan frame, 1024)}
	if resp.StatusCode != http.StatusOK {
		close(s.frames)
		return s
	}
	go func() {
		defer close(s.frames)
		var f frame
		sc := bufio.NewScanner(resp.Body)
		for sc.Scan() {
			line := sc.Text()
			switch {
			case line == "":
				s.frames <- f
				f = frame{}
			case strings.HasPrefix(line, "id: "):
				f.id = strings.TrimPrefix(line, "id: ")
			case strings.HasPrefix(line, "event: "):
				f.event = strings.TrimPrefix(line, "event: ")
			case strings.HasPrefix(line, "data: "):
				f.data = strings.TrimPrefix(line, "data: ")
			}
		}
	}()
	return s
}

// next returns the stream's next event, failing the test when none comes
// within frameTimeout or the stream ended.
func (s *stream) next(t *testing.T) frame {
	t.Helper()
	select {
	case f, ok := <-s.frames:
		if !ok {
			t.Fatalf("the stream ended")
		}
		return f
	case <-time.After(frameTimeout):
		t.Fatalf("no event within %v", frameTimeout)
	}
	return frame{}
}

func cursor(v string) *string { return &v }

func TestEvents_StreamsEventsAppendedAfterTheRequest(t *testing.T) {
	t.Parallel()
	e := newEventServer(t)
	e.append(t, 1, "test.before")

	s := e.openStream(t, nil)
	if s.resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", s.resp.StatusCode)
	}
	if ct := s.resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "text/event-stream") {
		t.Errorf("Content-Type = %q, want text/event-stream", ct)
	}
	e.append(t, 1, "test.a")
	e.append(t, 1, "test.b")

	f := s.next(t)
	if f.id != "2" || f.event != "test.a" {
		t.Errorf("first event = id %q event %q, want id 2 event test.a", f.id, f.event)
	}
	var data map[string]any
	if err := json.Unmarshal([]byte(f.data), &data); err != nil {
		t.Fatalf("data %q is not JSON: %v", f.data, err)
	}
	if data["seq"] != float64(2) || data["type"] != "test.a" || data["time"] != day0.Format(time.RFC3339Nano) {
		t.Errorf("data = %v, want seq 2, type test.a and time %s", data, day0.Format(time.RFC3339Nano))
	}
	if inner, ok := data["data"].(map[string]any); !ok || inner["k"] != "v" {
		t.Errorf("data.data = %v, want the appended data", data["data"])
	}
	if f := s.next(t); f.id != "3" || f.event != "test.b" {
		t.Errorf("second event = id %q event %q, want id 3 event test.b", f.id, f.event)
	}
}

func TestEvents_ReplaysFromTheCursorWithoutGapOrRepeat(t *testing.T) {
	t.Parallel()
	e := newEventServer(t)
	e.append(t, 200, "test.kept")

	appended := make(chan struct{})
	go func() {
		defer close(appended)
		e.append(t, 100, "test.live")
	}()
	s := e.openStream(t, cursor("150"))
	if s.resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", s.resp.StatusCode)
	}
	for want := 151; want <= 300; want++ {
		if f := s.next(t); f.id != strconv.Itoa(want) {
			t.Fatalf("event id %q, want %d", f.id, want)
		}
	}
	<-appended

	newest := e.openStream(t, cursor("300"))
	if newest.resp.StatusCode != http.StatusOK {
		t.Fatalf("status with the newest seq as cursor = %d, want 200", newest.resp.StatusCode)
	}
	select {
	case f := <-newest.frames:
		t.Fatalf("got event %q before any was appended", f.id)
	case <-time.After(50 * time.Millisecond):
	}
	e.append(t, 1, "test.next")
	if f := newest.next(t); f.id != "301" {
		t.Errorf("event id %q, want 301", f.id)
	}
}

func TestEvents_RefusesAnExpiredCursor(t *testing.T) {
	t.Parallel()
	tests := []struct {
		cursor        string
		status        int
		wantOldestSeq float64
		wantFirstID   string
	}{
		{cursor: "0", status: http.StatusGone, wantOldestSeq: 11},
		{cursor: "9", status: http.StatusGone, wantOldestSeq: 11},
		{cursor: "10", status: http.StatusOK, wantFirstID: "11"},
	}
	for _, tt := range tests {
		t.Run("Last-Event-ID "+tt.cursor, func(t *testing.T) {
			t.Parallel()
			e := newEventServer(t)
			e.append(t, 10, "test.removed")
			e.clock.Set(day0.AddDate(0, 0, 8))
			e.append(t, 10, "test.kept")
			if err := e.log.Prune(context.Background()); err != nil {
				t.Fatalf("Prune: %v", err)
			}

			s := e.openStream(t, cursor(tt.cursor))

			if s.resp.StatusCode != tt.status {
				t.Fatalf("status = %d, want %d", s.resp.StatusCode, tt.status)
			}
			if tt.status == http.StatusOK {
				if f := s.next(t); f.id != tt.wantFirstID {
					t.Errorf("first event id %q, want %s", f.id, tt.wantFirstID)
				}
				return
			}
			var body map[string]any
			if err := json.NewDecoder(s.resp.Body).Decode(&body); err != nil {
				t.Fatalf("body is not JSON: %v", err)
			}
			if body["error_type"] != "cursor_expired" || body["oldest_seq"] != tt.wantOldestSeq {
				t.Errorf("body = %v, want error_type cursor_expired and oldest_seq %v", body, tt.wantOldestSeq)
			}
			if msg, _ := body["message"].(string); msg == "" {
				t.Errorf("message is empty")
			}
		})
	}
}

func TestEvents_RefusesAMalformedCursor(t *testing.T) {
	t.Parallel()
	const newest = 3
	for _, c := range []string{"abc", "-1", "1.5", "", fmt.Sprint(newest + 1)} {
		t.Run(fmt.Sprintf("Last-Event-ID %q", c), func(t *testing.T) {
			t.Parallel()
			e := newEventServer(t)
			e.append(t, newest, "test.kept")
			r := httptest.NewRequest(http.MethodGet, "/api/v1/events", nil)
			r.Header.Set("Authorization", "Bearer "+clientToken)
			r.Header.Set("Last-Event-ID", c)
			w := httptest.NewRecorder()

			e.api.ServeHTTP(w, r)

			assertError(t, w, http.StatusBadRequest, "invalid_request")
		})
	}
}
