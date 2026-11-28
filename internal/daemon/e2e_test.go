package daemon_test

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"os"
	"slices"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/buildinfo"
	"github.com/agent-fox-dev/nightshift/internal/cli"
	"github.com/agent-fox-dev/nightshift/internal/daemon"
	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
)

// startTimeout bounds how long startDaemon waits for the daemon to listen
// and how long stop waits for it to return.
const startTimeout = 5 * time.Second

// syncBuffer is a buffer the daemon writes to while the test reads it.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// runningDaemon is a daemon started by startDaemon, running in the test
// process.
type runningDaemon struct {
	files
	// addr is the host:port the API listens on.
	addr string
	// d is the daemon, as Env.Started reported it.
	d       *daemon.Daemon
	stdout  *syncBuffer
	stderr  *syncBuffer
	signals chan os.Signal
	// done is closed once Run returned, with its exit code in code.
	done chan struct{}
	code int
}

// startDaemon runs daemon.Run on f's configuration in a goroutine, with a
// signal channel of the test's own in env.Signals, and returns once the API
// listens. The daemon is stopped with SIGTERM when the test ends, unless the
// test stopped it already.
func startDaemon(t *testing.T, f files, env daemon.Env) *runningDaemon {
	t.Helper()
	r := &runningDaemon{
		files:   f,
		stdout:  &syncBuffer{},
		stderr:  &syncBuffer{},
		signals: make(chan os.Signal, 1),
		done:    make(chan struct{}),
	}
	started := make(chan *daemon.Daemon, 1)
	env.Home, env.Stdout, env.Stderr, env.Signals = f.dir, r.stdout, r.stderr, r.signals
	env.Started = func(d *daemon.Daemon) { started <- d }
	go func() {
		defer close(r.done)
		r.code = daemon.Run([]string{"--config", f.config}, env)
	}()

	select {
	case r.d = <-started:
	case <-r.done:
		t.Fatalf("the daemon returned %d before it listened; stderr %q", r.code, r.stderr.String())
	case <-time.After(startTimeout):
		t.Fatalf("the daemon did not listen within %v; stderr %q", startTimeout, r.stderr.String())
	}
	r.addr = r.d.Addr()
	t.Cleanup(func() {
		if !r.exited() {
			r.stop(t)
		}
	})
	return r
}

// signal sends sig to the daemon.
func (r *runningDaemon) signal(sig os.Signal) { r.signals <- sig }

// exited reports whether Run has returned.
func (r *runningDaemon) exited() bool {
	select {
	case <-r.done:
		return true
	default:
		return false
	}
}

// wait waits for the daemon to return and gives its exit code.
func (r *runningDaemon) wait(t *testing.T) int {
	t.Helper()
	select {
	case <-r.done:
	case <-time.After(startTimeout):
		t.Fatalf("the daemon did not return within %v; stderr %q", startTimeout, r.stderr.String())
	}
	return r.code
}

// stop sends SIGTERM and waits for the daemon to return.
func (r *runningDaemon) stop(t *testing.T) int {
	t.Helper()
	r.signal(syscall.SIGTERM)
	return r.wait(t)
}

// status runs `nightshift status` against the daemon with its token file.
func (r *runningDaemon) status(args ...string) (int, string, string) {
	var stdout, stderr bytes.Buffer
	code := cli.Run(append([]string{"status", "--addr", r.addr, "--token-file", r.token}, args...), cli.Env{
		Home:   r.dir,
		Getenv: func(string) string { return "" },
		Stdout: &stdout,
		Stderr: &stderr,
	})
	return code, stdout.String(), stderr.String()
}

// hubLine is the line of `nightshift status` that reports the hub.
func (r *runningDaemon) hubLine(t *testing.T) string {
	t.Helper()
	code, stdout, stderr := r.status()
	if code != 0 {
		t.Fatalf("nightshift status exit code = %d (stderr %q)", code, stderr)
	}
	lines := strings.Split(stdout, "\n")
	if len(lines) < 2 {
		t.Fatalf("nightshift status printed %q, want a hub line", stdout)
	}
	return lines[1]
}

// hubRequest sends a request with the token of cred through the daemon's
// hub client, as any part of the daemon would, and returns its error.
func (r *runningDaemon) hubRequest(t *testing.T, cred hub.Credential) error {
	t.Helper()
	c := r.d.Hub()
	req, err := c.NewRequest(t.Context(), http.MethodGet, "/api/v1/elsewhere", nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := c.Do(t.Context(), cred, req)
	if err == nil {
		resp.Body.Close()
	}
	return err
}

// appendEvents appends n events to the daemon's event log, as the packages
// that produce events do.
func (r *runningDaemon) appendEvents(t *testing.T, n int) {
	t.Helper()
	for range n {
		if _, err := r.d.Log().Append(t.Context(), "test.event", map[string]string{"k": "v"}); err != nil {
			t.Fatalf("Append: %v", err)
		}
	}
}

// eventStream is an open response of GET /api/v1/events, whose event ids
// are read in the background.
type eventStream struct {
	ids chan string
}

// openStream requests the daemon's event stream, with Last-Event-ID set to
// cursor unless it is empty, and fails the test unless it answers 200.
func (r *runningDaemon) openStream(t *testing.T, cursor string) *eventStream {
	t.Helper()
	token, err := os.ReadFile(r.token)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+r.addr+"/api/v1/events", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(token)))
	if cursor != "" {
		req.Header.Set("Last-Event-ID", cursor)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET /api/v1/events: %v", err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET /api/v1/events answered %d, want 200", resp.StatusCode)
	}
	s := &eventStream{ids: make(chan string, 1024)}
	go func() {
		defer close(s.ids)
		sc := bufio.NewScanner(resp.Body)
		for sc.Scan() {
			if id, ok := strings.CutPrefix(sc.Text(), "id: "); ok {
				s.ids <- id
			}
		}
	}()
	return s
}

// next returns the ids of the stream's next n events.
func (s *eventStream) next(t *testing.T, n int) []string {
	t.Helper()
	var ids []string
	for range n {
		select {
		case id, ok := <-s.ids:
			if !ok {
				t.Fatalf("the stream ended after ids %v, want %d", ids, n)
			}
			ids = append(ids, id)
		case <-time.After(startTimeout):
			t.Fatalf("no event within %v after ids %v", startTimeout, ids)
		}
	}
	return ids
}

// ended reports whether the stream ends, with no further event, within
// startTimeout.
func (s *eventStream) ended() bool {
	select {
	case _, ok := <-s.ids:
		return !ok
	case <-time.After(startTimeout):
		return false
	}
}

func TestDaemon_FirstStartAnswersTheStatus(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	f := writeConfig(t, hubLines(h.URL(), worker, approver))

	r := startDaemon(t, f, daemon.Env{})
	code, stdout, stderr := r.status()

	if code != 0 {
		t.Errorf("nightshift status exit code = %d, want 0 (stderr %q)", code, stderr)
	}
	if want := "nightshiftd " + buildinfo.Version + "\nhub: " + h.URL() + " reachable\n"; stdout != want {
		t.Errorf("nightshift status printed %q, want %q", stdout, want)
	}
	info, err := os.Stat(f.token)
	if err != nil {
		t.Fatalf("token file: %v", err)
	}
	if mode := info.Mode().Perm(); mode != 0o600 {
		t.Errorf("token file mode = %o, want 600", mode)
	}
	if _, err := os.Stat(f.store); err != nil {
		t.Errorf("store file: %v", err)
	}
	resp, err := http.Get("http://" + r.addr + "/api/v1/status")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var body struct {
		ErrorType string `json:"error_type"`
	}
	json.NewDecoder(resp.Body).Decode(&body)
	if resp.StatusCode != http.StatusUnauthorized || body.ErrorType != "unauthorized" {
		t.Errorf("a status request without the token answered %d %q, want 401 unauthorized", resp.StatusCode, body.ErrorType)
	}
	if code := r.stop(t); code != 0 {
		t.Errorf("the daemon returned %d after SIGTERM, want 0", code)
	}
}

func TestDaemon_StreamResumesAfterARestart(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	f := writeConfig(t, hubLines(h.URL(), worker, approver))

	first := startDaemon(t, f, daemon.Env{})
	s := first.openStream(t, "")
	first.appendEvents(t, 3)
	if ids := s.next(t, 3); !slices.Equal(ids, []string{"1", "2", "3"}) {
		t.Errorf("the stream delivered ids %v, want 1 2 3", ids)
	}
	if code := first.stop(t); code != 0 {
		t.Errorf("the first daemon returned %d after SIGTERM, want 0", code)
	}
	if !s.ended() {
		t.Error("the stream did not end when the daemon stopped")
	}

	second := startDaemon(t, f, daemon.Env{})
	second.appendEvents(t, 2)
	resumed := second.openStream(t, "3")
	if ids := resumed.next(t, 2); !slices.Equal(ids, []string{"4", "5"}) {
		t.Errorf("the resumed stream delivered ids %v, want 4 5", ids)
	}
	if code := second.stop(t); code != 0 {
		t.Errorf("the second daemon returned %d after SIGTERM, want 0", code)
	}

	// A start eight days later prunes every event, so the successor of the
	// client's cursor is gone.
	later := time.Now().Add(8 * 24 * time.Hour)
	third := startDaemon(t, f, daemon.Env{Clock: func() time.Time { return later }})
	status, body := third.getEvents(t, "3")
	if status != http.StatusGone || body.ErrorType != "cursor_expired" || body.OldestSeq != 6 {
		t.Errorf("a reconnect after the prune answered %d %q oldest_seq %d, want 410 cursor_expired oldest_seq 6",
			status, body.ErrorType, body.OldestSeq)
	}
}

// eventsRefusal is the error body of a refused GET /api/v1/events.
type eventsRefusal struct {
	ErrorType string `json:"error_type"`
	OldestSeq int64  `json:"oldest_seq"`
}

// getEvents requests the daemon's event stream with Last-Event-ID cursor and
// returns the status and, for a refusal, its error body; a stream that is
// accepted is closed at once.
func (r *runningDaemon) getEvents(t *testing.T, cursor string) (int, eventsRefusal) {
	t.Helper()
	token, err := os.ReadFile(r.token)
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequestWithContext(t.Context(), http.MethodGet, "http://"+r.addr+"/api/v1/events", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(token)))
	req.Header.Set("Last-Event-ID", cursor)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("GET /api/v1/events: %v", err)
	}
	defer resp.Body.Close()
	var body eventsRefusal
	if resp.StatusCode != http.StatusOK {
		json.NewDecoder(resp.Body).Decode(&body)
	}
	return resp.StatusCode, body
}

func TestDaemon_RidesOutAHubOutage(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	f := writeConfig(t, hubLines(h.URL(), worker, approver))
	r := startDaemon(t, f, daemon.Env{ProbeInterval: 20 * time.Millisecond})

	h.Stop()
	if err := r.hubRequest(t, hub.Worker); err == nil {
		t.Fatal("a request to the stopped hub succeeded")
	}
	if line := r.hubLine(t); !strings.HasPrefix(line, "hub: unreachable since ") {
		t.Errorf("while the hub is down, nightshift status reports %q, want \"hub: unreachable since <time>\"", line)
	}

	h.Start(t)
	want := "hub: " + h.URL() + " reachable"
	deadline := time.Now().Add(startTimeout)
	for line := r.hubLine(t); line != want; line = r.hubLine(t) {
		if time.Now().After(deadline) {
			t.Fatalf("after the hub came back, nightshift status reports %q, want %q", line, want)
		}
		time.Sleep(5 * time.Millisecond)
	}
	if r.exited() {
		t.Errorf("the daemon returned %d during the outage", r.code)
	}
}
