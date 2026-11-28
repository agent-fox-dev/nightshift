package daemon_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/buildinfo"
	"github.com/agent-fox-dev/nightshift/internal/cli"
	"github.com/agent-fox-dev/nightshift/internal/daemon"
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
	done    chan int

	once sync.Once
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
		done:    make(chan int, 1),
	}
	started := make(chan *daemon.Daemon, 1)
	env.Home, env.Stdout, env.Stderr, env.Signals = f.dir, r.stdout, r.stderr, r.signals
	env.Started = func(d *daemon.Daemon) { started <- d }
	go func() { r.done <- daemon.Run([]string{"--config", f.config}, env) }()

	select {
	case r.d = <-started:
	case code := <-r.done:
		t.Fatalf("the daemon returned %d before it listened; stderr %q", code, r.stderr.String())
	case <-time.After(startTimeout):
		t.Fatalf("the daemon did not listen within %v; stderr %q", startTimeout, r.stderr.String())
	}
	r.addr = r.d.Addr()
	t.Cleanup(func() {
		select {
		case <-r.done:
			return
		default:
		}
		r.stop(t)
	})
	return r
}

// signal sends sig to the daemon.
func (r *runningDaemon) signal(sig os.Signal) { r.signals <- sig }

// wait waits for the daemon to return and gives its exit code.
func (r *runningDaemon) wait(t *testing.T) int {
	t.Helper()
	r.once.Do(func() {
		select {
		case r.code = <-r.done:
		case <-time.After(startTimeout):
			t.Fatalf("the daemon did not return within %v; stderr %q", startTimeout, r.stderr.String())
		}
	})
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
