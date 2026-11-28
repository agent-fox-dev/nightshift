package daemon_test

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/daemon"
	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
	"github.com/agent-fox-dev/nightshift/internal/store"
)

// daemonConfigEnv, when set in the environment of the test binary, makes it
// run as nightshiftd on the configuration file it names, so that a test can
// send a real signal to a daemon process.
const daemonConfigEnv = "NIGHTSHIFT_TEST_DAEMON_CONFIG"

func TestMain(m *testing.M) {
	if config := os.Getenv(daemonConfigEnv); config != "" {
		os.Exit(daemon.Run([]string{"--config", config}, daemon.Env{
			Home:   filepath.Dir(config),
			Stdout: os.Stdout,
			Stderr: os.Stderr,
			// The test learns the address the daemon listens on from this
			// line.
			Started: func(d *daemon.Daemon) { fmt.Printf("listening %s\n", d.Addr()) },
		}))
	}
	os.Exit(m.Run())
}

// hubState is the hub member of GET /api/v1/status.
type hubState struct {
	Endpoint         string  `json:"endpoint"`
	Reachable        bool    `json:"reachable"`
	UnreachableSince *string `json:"unreachable_since"`
	CredentialError  *string `json:"credential_error"`
}

// hubState asks the daemon's status and returns its hub member.
func (r *runningDaemon) hubState(t *testing.T) hubState {
	t.Helper()
	code, stdout, stderr := r.status("--json")
	if code != 0 {
		t.Fatalf("nightshift status --json exit code = %d (stderr %q)", code, stderr)
	}
	var body struct {
		Hub hubState `json:"hub"`
	}
	if err := json.Unmarshal([]byte(stdout), &body); err != nil {
		t.Fatalf("status body %q: %v", stdout, err)
	}
	return body.Hub
}

// lastBearer is the credential of the last request the hub saw.
func lastBearer(t *testing.T, h *hubtest.Hub) string {
	t.Helper()
	reqs := h.Requests()
	if len(reqs) == 0 {
		t.Fatal("the hub saw no request")
	}
	return reqs[len(reqs)-1].Bearer
}

// revokedWorker starts a daemon whose worker token the hub then revokes,
// and makes the daemon send it once, so that the hub answers 401.
func revokedWorker(t *testing.T) (h *hubtest.Hub, r *runningDaemon, worker, approver string) {
	t.Helper()
	h = hubtest.New(t)
	worker, approver = h.GoodTokens()
	f := writeConfig(t, hubLines(h.URL(), worker, approver))
	r = startDaemon(t, f, daemon.Env{})
	h.Revoke(worker)
	err := r.hubRequest(t, hub.Worker)
	var he *hub.Error
	if !errors.As(err, &he) || he.Status != http.StatusUnauthorized {
		t.Fatalf("a request with the revoked worker token returned %v, want a 401", err)
	}
	return h, r, worker, approver
}

func TestDaemon_RecordsATokenTheHubStopsAccepting(t *testing.T) {
	t.Parallel()
	_, r, _, _ := revokedWorker(t)

	c := r.d.Hub()
	if c.Available(hub.Worker) {
		t.Error("Available(worker) = true after the hub answered its token 401, want false")
	}
	if !c.Available(hub.Approver) {
		t.Error("Available(approver) = false, want true: only the worker token was refused")
	}
	st := r.hubState(t)
	if want := "worker token: 401 unauthenticated"; st.CredentialError == nil || *st.CredentialError != want {
		t.Errorf("status hub.credential_error = %v, want %q", st.CredentialError, want)
	}
	if !st.Reachable {
		t.Error("status hub.reachable = false after a 401, want true")
	}
	if r.exited() {
		t.Errorf("the daemon returned %d after the 401", r.code)
	}
}

func TestDaemon_ReloadTakesOnlyTokensThatPassTheCheck(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		// hub is the [hub] table the configuration is rewritten with; it
		// may add tokens to the fake hub, and returns the worker token a
		// passing reload takes, or "" for a failing one.
		hub        func(h *hubtest.Hub, worker, approver string) (lines, newWorker string)
		wantStderr string
	}{
		{
			name: "a new good worker token",
			hub: func(h *hubtest.Hub, _, approver string) (string, string) {
				fresh := h.AddToken(hubtest.Token{Name: "worker 2", Permissions: hubtest.WorkerScopes()})
				return hubLines(h.URL(), fresh, approver), fresh
			},
		},
		{
			name: "worker and approver the same token",
			hub: func(h *hubtest.Hub, _, approver string) (string, string) {
				return hubLines(h.URL(), approver, approver), ""
			},
			wantStderr: "credentials: worker and approver are the same token\n",
		},
		{
			name: "a configuration that does not load",
			hub: func(h *hubtest.Hub, worker, approver string) (string, string) {
				return hubLines(h.URL(), worker, approver) + "endpont = \"elsewhere\"\n", ""
			},
			wantStderr: "config: unknown key \"hub.endpont\"\n",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h, r, worker, approver := revokedWorker(t)
			lines, newWorker := tt.hub(h, worker, approver)
			r.files.rewrite(t, lines)

			err := r.d.Reload(t.Context())

			if got := r.stderr.String(); got != tt.wantStderr {
				t.Errorf("stderr = %q, want %q", got, tt.wantStderr)
			}
			if (err == nil) != (newWorker != "") {
				t.Errorf("Reload() = %v", err)
			}
			st := r.hubState(t)
			r.hubRequest(t, hub.Worker)
			got := lastBearer(t, h)
			if newWorker != "" {
				if st.CredentialError != nil {
					t.Errorf("status hub.credential_error = %q after a passing reload, want null", *st.CredentialError)
				}
				if got != newWorker {
					t.Errorf("the next hub request bore %q, want the new worker token %q", got, newWorker)
				}
			} else if got != worker {
				t.Errorf("the next hub request bore %q, want the previous worker token %q", got, worker)
			}
			if r.exited() {
				t.Errorf("the daemon returned %d on a reload", r.code)
			}
		})
	}
}

func TestDaemon_RealSignalsReloadAndStop(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	f := writeConfig(t, hubLines(h.URL(), worker, approver))

	cmd := exec.Command(os.Args[0], "-test.run=^$")
	cmd.Env = append(os.Environ(), daemonConfigEnv+"="+f.config)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	stderr := &syncBuffer{}
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	// exited is closed once the process ended, with Wait's error in waitErr.
	exited := make(chan struct{})
	var waitErr error
	lines := make(chan string, 1)
	go func() {
		sc := bufio.NewScanner(stdout)
		for sc.Scan() {
			if addr, ok := strings.CutPrefix(sc.Text(), "listening "); ok {
				lines <- addr
			}
		}
		// Wait only once stdout is read to its end, as StdoutPipe requires.
		waitErr = cmd.Wait()
		close(exited)
	}()
	t.Cleanup(func() {
		cmd.Process.Kill()
		<-exited
	})
	var addr string
	select {
	case addr = <-lines:
	case <-exited:
		t.Fatalf("the daemon process exited before it listened: %v; stderr %q", waitErr, stderr.String())
	case <-time.After(startTimeout):
		t.Fatalf("the daemon process did not listen within %v", startTimeout)
	}

	fresh := h.AddToken(hubtest.Token{Name: "worker 2", Permissions: hubtest.WorkerScopes()})
	f.rewrite(t, hubLines(h.URL(), fresh, approver))
	if err := cmd.Process.Signal(syscall.SIGHUP); err != nil {
		t.Fatal(err)
	}
	// The reload checks the new tokens, so the hub sees the new worker token.
	deadline := time.Now().Add(startTimeout)
	for len(h.Bearing(fresh)) == 0 {
		select {
		case <-exited:
			t.Fatalf("the daemon process exited on SIGHUP: %v", waitErr)
		default:
		}
		if time.Now().After(deadline) {
			t.Fatalf("the hub saw no request with the reloaded worker token within %v", startTimeout)
		}
		time.Sleep(5 * time.Millisecond)
	}
	r := &runningDaemon{files: f, addr: addr}
	if code, _, errText := r.status(); code != 0 {
		t.Errorf("nightshift status after SIGHUP exit code = %d (%s)", code, errText)
	}

	if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	select {
	case <-exited:
		if waitErr != nil {
			t.Errorf("the daemon process ended with %v after SIGTERM, want exit code 0; stderr %q", waitErr, stderr.String())
		}
	case <-time.After(startTimeout):
		t.Fatalf("the daemon process did not exit within %v of SIGTERM", startTimeout)
	}
}

func TestDaemon_StopsCleanlyOnASignal(t *testing.T) {
	t.Parallel()
	for _, sig := range []os.Signal{syscall.SIGTERM, syscall.SIGINT} {
		t.Run(sig.String(), func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			worker, approver := h.GoodTokens()
			f := writeConfig(t, hubLines(h.URL(), worker, approver))
			r := startDaemon(t, f, daemon.Env{})
			var calls atomic.Int32
			r.d.OnShutdown("count", func(context.Context) error {
				calls.Add(1)
				return nil
			})
			s := r.openStream(t, "")

			r.signal(sig)

			if code := r.wait(t); code != 0 {
				t.Errorf("exit code = %d, want 0", code)
			}
			if n := calls.Load(); n != 1 {
				t.Errorf("the shutdown step ran %d times, want once", n)
			}
			if !s.ended() {
				t.Error("the event stream did not end")
			}
			if conn, err := net.DialTimeout("tcp", r.addr, time.Second); err == nil {
				conn.Close()
				t.Errorf("a connection to %s after the stop was accepted, want it refused", r.addr)
			}
			st, err := store.Open(f.store)
			if err != nil {
				t.Fatalf("store.Open after the stop: %v", err)
			}
			st.Close()
		})
	}
}

func TestDaemon_AbandonsAStuckShutdownStep(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	f := writeConfig(t, hubLines(h.URL(), worker, approver))
	r := startDaemon(t, f, daemon.Env{StepDeadline: 100 * time.Millisecond, ExitDeadline: 200 * time.Millisecond})
	release := make(chan struct{})
	// The step ignores its context, as a stuck one does; it is let go when
	// the test ends, so that its goroutine does not outlive the test.
	t.Cleanup(func() { close(release) })
	r.d.OnShutdown("stuck", func(context.Context) error {
		<-release
		return nil
	})

	start := time.Now()
	r.signal(syscall.SIGTERM)
	code := r.wait(t)
	elapsed := time.Since(start)

	if code != 0 {
		t.Errorf("exit code = %d, want 0", code)
	}
	if elapsed >= 200*time.Millisecond {
		t.Errorf("the daemon returned %v after the signal, want under 200ms", elapsed)
	}
	if got := r.stderr.String(); !strings.Contains(got, `"stuck"`) {
		t.Errorf("stderr = %q, want a line naming the step \"stuck\"", got)
	}
}

func TestDaemon_ClosesAStreamThatStopsReading(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	f := writeConfig(t, hubLines(h.URL(), worker, approver))
	r := startDaemon(t, f, daemon.Env{StepDeadline: 2 * time.Second, ExitDeadline: 3 * time.Second})
	var calls atomic.Int32
	r.d.OnShutdown("count", func(context.Context) error {
		calls.Add(1)
		return nil
	})

	// A client that requests the stream and then never reads, with a small
	// receive buffer, so that the daemon's writes to it block.
	token, err := os.ReadFile(f.token)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := net.Dial("tcp", r.addr)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	conn.(*net.TCPConn).SetReadBuffer(4096)
	fmt.Fprintf(conn, "GET /api/v1/events HTTP/1.1\r\nHost: nightshiftd\r\nAuthorization: Bearer %s\r\n\r\n", strings.TrimSpace(string(token)))
	waitFor(t, "the stream to subscribe", func() bool { return r.d.Log().Subscribers() == 1 })

	big := strings.Repeat("x", 64<<10)
	for range 2 * 256 {
		if _, err := r.d.Log().Append(t.Context(), "test.big", big); err != nil {
			t.Fatal(err)
		}
	}
	if n := r.d.Log().Subscribers(); n != 0 {
		t.Fatalf("Subscribers() = %d after the stalled client fell behind, want 0", n)
	}
	// The stalled client does not hold up a stop, which still runs every
	// shutdown step.
	start := time.Now()
	if code := r.stop(t); code != 0 {
		t.Errorf("exit code = %d, want 0", code)
	}
	if elapsed := time.Since(start); elapsed >= time.Second {
		t.Errorf("the stop took %v, want well under the step deadline", elapsed)
	}
	if n := calls.Load(); n != 1 {
		t.Errorf("the shutdown step ran %d times, want once", n)
	}
	// The connection was closed: reading what was sent ends, rather than
	// waiting for more.
	conn.SetReadDeadline(time.Now().Add(startTimeout))
	if _, err := io.Copy(io.Discard, conn); err != nil {
		t.Errorf("reading the dropped stream ended with %v, want the connection closed", err)
	}
}

// waitFor fails the test unless cond becomes true within startTimeout.
func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(startTimeout)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}
