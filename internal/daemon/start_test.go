package daemon_test

import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/daemon"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
)

// files are the paths a daemon started from writeConfig's file would create.
type files struct {
	dir, config, token, store string
}

// writeConfig writes a configuration file into a directory of the test's
// own, with the [hub] table's lines hub, and returns its paths.
func writeConfig(t *testing.T, hub string) files {
	t.Helper()
	dir := t.TempDir()
	f := files{
		dir:    dir,
		config: filepath.Join(dir, "config.toml"),
		token:  filepath.Join(dir, "token"),
		store:  filepath.Join(dir, "store", "store.db"),
	}
	content := "listen = \"127.0.0.1:0\"\n" +
		"token_file = \"" + f.token + "\"\n" +
		"[store]\npath = \"" + f.store + "\"\n" +
		"[hub]\n" + hub
	if err := os.WriteFile(f.config, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return f
}

// hubLines is a [hub] table naming endpoint and the two tokens.
func hubLines(endpoint, worker, approver string) string {
	return fmt.Sprintf("endpoint = %q\nworker_token = %q\napprover_token = %q\n", endpoint, worker, approver)
}

// run runs the daemon on f's configuration and returns its exit code and
// stderr.
func run(t *testing.T, f files, env daemon.Env) (int, string) {
	t.Helper()
	var stdout, stderr bytes.Buffer
	env.Home, env.Stdout, env.Stderr = f.dir, &stdout, &stderr
	code := daemon.Run([]string{"--config", f.config}, env)
	return code, stderr.String()
}

// assertNothingCreated fails the test when the daemon created its token file
// or its store.
func assertNothingCreated(t *testing.T, f files) {
	t.Helper()
	for _, path := range []string{f.token, f.store} {
		if _, err := os.Stat(path); !errors.Is(err, fs.ErrNotExist) {
			t.Errorf("%s exists (stat error %v), want it not created", path, err)
		}
	}
}

func TestRun_ConfigFailureStartsNothing(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	f := writeConfig(t, "worker_token = \"parley_pat_w1_secret\"\napprover_token = \"parley_pat_a1_secret\"\n")

	code, stderr := run(t, f, daemon.Env{})

	if code != 1 {
		t.Errorf("exit code = %d, want 1", code)
	}
	if want := "config: hub.endpoint is not set\n"; stderr != want {
		t.Errorf("stderr = %q, want %q", stderr, want)
	}
	if n := len(h.Requests()); n != 0 {
		t.Errorf("the hub saw %d requests, want none", n)
	}
	assertNothingCreated(t, f)
}

func TestRun_ChecksRunInOrderAndStopAtTheFirstFailure(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		// setup scripts the hub and returns the two configured tokens.
		setup      func(h *hubtest.Hub) (worker, approver string)
		wantPrefix string
		// wantAPI is whether the hub may have seen requests under /api/v1.
		wantAPI bool
	}{
		{
			name: "hub fails before credentials are read",
			setup: func(h *hubtest.Hub) (string, string) {
				h.SetReady(http.StatusServiceUnavailable)
				// Tokens the hub does not know: it would answer them 401.
				return "parley_pat_w1_secret", "parley_pat_a1_secret"
			},
			wantPrefix: "hub: ",
		},
		{
			name: "credentials fail after the hub answered",
			setup: func(h *hubtest.Hub) (string, string) {
				return "parley_pat_w1_secret", "parley_pat_a1_secret"
			},
			wantPrefix: "credentials: ",
			wantAPI:    true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			worker, approver := tt.setup(h)
			f := writeConfig(t, hubLines(h.URL(), worker, approver))

			code, stderr := run(t, f, daemon.Env{})

			if code != 1 {
				t.Errorf("exit code = %d, want 1", code)
			}
			if lines := strings.Split(strings.TrimSuffix(stderr, "\n"), "\n"); len(lines) != 1 || !strings.HasPrefix(stderr, tt.wantPrefix) {
				t.Errorf("stderr = %q, want one line starting %q", stderr, tt.wantPrefix)
			}
			if n := h.APIRequests(); !tt.wantAPI && n != 0 {
				t.Errorf("the hub saw %d requests under /api/v1, want none", n)
			}
			assertNothingCreated(t, f)
		})
	}
}

func TestRun_HubCheckFailures(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name  string
		setup func(h *hubtest.Hub)
		want  string
	}{
		{"connection refused", func(h *hubtest.Hub) { h.Stop() }, "hub: connection refused\n"},
		{"readyz not ready", func(h *hubtest.Hub) { h.SetReady(http.StatusServiceUnavailable) }, "hub: readyz answered 503\n"},
		{"readyz never answers", func(h *hubtest.Hub) { h.SetReady(hubtest.Hang) }, "hub: timeout\n"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			worker, approver := h.GoodTokens()
			tt.setup(h)
			f := writeConfig(t, hubLines(h.URL(), worker, approver))

			code, stderr := run(t, f, daemon.Env{HubTimeout: 50 * time.Millisecond})

			if code != 1 {
				t.Errorf("exit code = %d, want 1", code)
			}
			if stderr != tt.want {
				t.Errorf("stderr = %q, want %q", stderr, tt.want)
			}
			assertNothingCreated(t, f)
		})
	}
}

func TestRun_FailuresAfterTheChecksServeNothing(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		// edit changes the configuration's paths and listen address so that
		// one of them fails, and returns the edited file's content.
		edit       func(t *testing.T, f files, h *hubtest.Hub) string
		wantPrefix string
	}{
		{
			name: "store path under a regular file",
			edit: func(t *testing.T, f files, h *hubtest.Hub) string {
				notADir := filepath.Join(f.dir, "plain")
				if err := os.WriteFile(notADir, nil, 0o600); err != nil {
					t.Fatal(err)
				}
				return configWith(t, h, "127.0.0.1:0", f.token, filepath.Join(notADir, "store.db"))
			},
			wantPrefix: "store: ",
		},
		{
			name: "token file in a read-only directory",
			edit: func(t *testing.T, f files, h *hubtest.Hub) string {
				readOnly := filepath.Join(f.dir, "read-only")
				if err := os.Mkdir(readOnly, 0o500); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { os.Chmod(readOnly, 0o700) })
				return configWith(t, h, "127.0.0.1:0", filepath.Join(readOnly, "token"), f.store)
			},
			wantPrefix: "token: ",
		},
		{
			name: "listen address already held",
			edit: func(t *testing.T, f files, h *hubtest.Hub) string {
				ln, err := net.Listen("tcp", "127.0.0.1:0")
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { ln.Close() })
				return configWith(t, h, ln.Addr().String(), f.token, f.store)
			},
			wantPrefix: "listen: ",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			f := writeConfig(t, "")
			if err := os.WriteFile(f.config, []byte(tt.edit(t, f, h)), 0o600); err != nil {
				t.Fatal(err)
			}
			// A daemon that serves anyway stops at once on this signal, so
			// that the test ends and reports it.
			signals := make(chan os.Signal, 1)
			signals <- syscall.SIGTERM

			code, stderr := run(t, f, daemon.Env{Signals: signals})

			if code != 1 {
				t.Errorf("exit code = %d, want 1", code)
			}
			if lines := strings.Split(strings.TrimSuffix(stderr, "\n"), "\n"); len(lines) != 1 || !strings.HasPrefix(stderr, tt.wantPrefix) {
				t.Errorf("stderr = %q, want one line starting %q", stderr, tt.wantPrefix)
			}
		})
	}
}

// configWith is a configuration against h's two good tokens with the
// listen address and the paths given.
func configWith(t *testing.T, h *hubtest.Hub, listen, token, store string) string {
	t.Helper()
	worker, approver := h.GoodTokens()
	return fmt.Sprintf("listen = %q\ntoken_file = %q\n[store]\npath = %q\n[hub]\n", listen, token, store) +
		hubLines(h.URL(), worker, approver)
}
