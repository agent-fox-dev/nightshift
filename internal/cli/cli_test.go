package cli_test

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/cli"
)

// show-addr, a command of the tests' own, prints the address a command
// sends its requests to, so that the default address is checked without a
// daemon listening on it.
func init() {
	cli.Register(cli.Command{
		Name:     "show-addr",
		Synopsis: "show-addr",
		Summary:  "print the daemon's address as resolved",
		Setup: func(*flag.FlagSet) func(*cli.Call, []string) int {
			return func(c *cli.Call, _ []string) int {
				fmt.Fprintln(c.Stdout, c.Addr())
				return 0
			}
		},
	})
}

// fakeDaemon stands in for nightshiftd: it answers every request with one
// status and body, and records each request's path and Authorization
// header.
type fakeDaemon struct {
	srv *httptest.Server

	mu       sync.Mutex
	requests []seen
}

type seen struct{ path, authorization string }

func newFakeDaemon(t *testing.T, status int, body string) *fakeDaemon {
	t.Helper()
	return serveFake(t, httptest.NewUnstartedServer(nil), status, body)
}

// serveFake starts srv as a fake daemon answering status and body.
func serveFake(t *testing.T, srv *httptest.Server, status int, body string) *fakeDaemon {
	t.Helper()
	f := &fakeDaemon{srv: srv}
	srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.requests = append(f.requests, seen{r.URL.Path, r.Header.Get("Authorization")})
		f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		w.Write([]byte(body))
	})
	srv.Start()
	t.Cleanup(srv.Close)
	return f
}

func (f *fakeDaemon) addr() string { return f.srv.Listener.Addr().String() }

func (f *fakeDaemon) seen() []seen {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]seen(nil), f.requests...)
}

// client is a home directory with a client token in its default place and
// the environment the client runs in.
type client struct {
	home, token string
	vars        map[string]string
}

func newClient(t *testing.T) *client {
	t.Helper()
	c := &client{home: t.TempDir(), token: "home-token", vars: map[string]string{}}
	writeToken(t, filepath.Join(c.home, ".nightshift", "token"), c.token)
	return c
}

func writeToken(t *testing.T, path, token string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(token+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
}

// run runs the client with args and returns its exit code, stdout and
// stderr.
func (c *client) run(args ...string) (int, string, string) {
	var stdout, stderr bytes.Buffer
	code := cli.Run(args, cli.Env{
		Home:   c.home,
		Getenv: func(k string) string { return c.vars[k] },
		Stdout: &stdout,
		Stderr: &stderr,
	})
	return code, stdout.String(), stderr.String()
}

// closedAddr is an address on which nothing listens.
func closedAddr(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	ln.Close()
	return addr
}

const reachableStatus = `{"version":"1.2.3","hub":{"endpoint":"https://hub.example","reachable":true,"unreachable_since":null,"credential_error":null},"sessions":[],"workspaces":[]}`

func TestStatus_PrintsTheDaemonsState(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		body string
		json bool
		want string
	}{
		{
			name: "reachable",
			body: reachableStatus,
			want: "nightshiftd 1.2.3\nhub: https://hub.example reachable\n",
		},
		{
			name: "unreachable",
			body: `{"version":"1.2.3","hub":{"endpoint":"https://hub.example","reachable":false,"unreachable_since":"2026-11-28T10:00:00Z","credential_error":null},"sessions":[],"workspaces":[]}`,
			want: "nightshiftd 1.2.3\nhub: unreachable since 2026-11-28T10:00:00Z\n",
		},
		{
			name: "credential error",
			body: `{"version":"1.2.3","hub":{"endpoint":"https://hub.example","reachable":true,"unreachable_since":null,"credential_error":"worker token: 401 unauthenticated"},"sessions":[],"workspaces":[]}`,
			want: "nightshiftd 1.2.3\nhub: https://hub.example reachable\nworker token: 401 unauthenticated\n",
		},
		{
			name: "json",
			body: reachableStatus,
			json: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			d := newFakeDaemon(t, http.StatusOK, tt.body)
			c := newClient(t)
			args := []string{"status", "--addr", d.addr()}
			if tt.json {
				args = append(args, "--json")
			}

			code, stdout, stderr := c.run(args...)

			if code != 0 {
				t.Fatalf("exit code = %d, want 0 (stderr %q)", code, stderr)
			}
			if tt.json {
				var got, want any
				if err := json.Unmarshal([]byte(stdout), &got); err != nil {
					t.Fatalf("stdout %q is not JSON: %v", stdout, err)
				}
				json.Unmarshal([]byte(tt.body), &want)
				if !reflect.DeepEqual(got, want) {
					t.Errorf("stdout = %v, want the served %v", got, want)
				}
			} else if stdout != tt.want {
				t.Errorf("stdout = %q, want %q", stdout, tt.want)
			}
			if got := d.seen(); len(got) != 1 || got[0].path != "/api/v1/status" {
				t.Errorf("the daemon saw %v, want one request for /api/v1/status", got)
			}
		})
	}
}

func TestRun_ResolvesAddressAndToken(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		// flagAddr, envAddr and tokenFile say which of --addr,
		// $NIGHTSHIFT_ADDR and --token-file the command line uses.
		flagAddr, envAddr, tokenFile bool
		// defaultAddr runs the daemon on the default address.
		defaultAddr bool
	}{
		{name: "flag before the environment", flagAddr: true, envAddr: true},
		{name: "environment only", envAddr: true},
		{name: "neither gives the default address", defaultAddr: true},
		{name: "token file flag", flagAddr: true, tokenFile: true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			c := newClient(t)
			var want, other *fakeDaemon
			var args []string
			switch {
			case tt.defaultAddr:
				// The default address is fixed and may be another daemon's,
				// so the address is read from the command, not served.
				code, stdout, stderr := c.run("show-addr")
				if code != 0 || stdout != "127.0.0.1:7878\n" {
					t.Errorf("show-addr = %d %q (stderr %q), want 0 \"127.0.0.1:7878\\n\"", code, stdout, stderr)
				}
				return
			case tt.flagAddr:
				want = newFakeDaemon(t, http.StatusOK, reachableStatus)
				args = append(args, "--addr", want.addr())
				if tt.envAddr {
					other = newFakeDaemon(t, http.StatusOK, reachableStatus)
					c.vars["NIGHTSHIFT_ADDR"] = other.addr()
				}
			default:
				want = newFakeDaemon(t, http.StatusOK, reachableStatus)
				c.vars["NIGHTSHIFT_ADDR"] = want.addr()
			}
			wantToken := c.token
			if tt.tokenFile {
				wantToken = "flag-token"
				path := filepath.Join(t.TempDir(), "elsewhere")
				writeToken(t, path, wantToken)
				args = append(args, "--token-file", path)
			}

			code, _, stderr := c.run(append([]string{"status"}, args...)...)

			if code != 0 {
				t.Fatalf("exit code = %d, want 0 (stderr %q)", code, stderr)
			}
			if got := want.seen(); len(got) != 1 || got[0].authorization != "Bearer "+wantToken {
				t.Errorf("the daemon saw %v, want one request bearing %q", got, wantToken)
			}
			if other != nil && len(other.seen()) != 0 {
				t.Errorf("the environment's daemon saw %v, want nothing", other.seen())
			}
		})
	}
}

func TestRun_FailsWithoutDaemonOrToken(t *testing.T) {
	t.Parallel()
	addr := closedAddr(t)
	missing := filepath.Join(t.TempDir(), "missing")
	tests := []struct {
		name string
		args []string
		want string
	}{
		{"daemon unreachable", []string{"status", "--addr", addr}, "cannot reach nightshiftd at " + addr + "\n"},
		{"token file missing", []string{"status", "--addr", addr, "--token-file", missing}, "cannot read the client token at " + missing + "\n"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			code, stdout, stderr := newClient(t).run(tt.args...)

			if code != 1 {
				t.Errorf("exit code = %d, want 1", code)
			}
			if stderr != tt.want {
				t.Errorf("stderr = %q, want %q", stderr, tt.want)
			}
			if stdout != "" {
				t.Errorf("stdout = %q, want nothing", stdout)
			}
		})
	}
}

func TestRun_PrintsTheErrorMessageOfAnErrorAnswer(t *testing.T) {
	t.Parallel()
	const body = `{"error_type":"unauthorized","message":"the client token is missing or wrong"}`
	for _, asJSON := range []bool{false, true} {
		name := "text"
		if asJSON {
			name = "json"
		}
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			d := newFakeDaemon(t, http.StatusUnauthorized, body)
			args := []string{"status", "--addr", d.addr()}
			if asJSON {
				args = append(args, "--json")
			}

			code, stdout, stderr := newClient(t).run(args...)

			if code != 1 {
				t.Errorf("exit code = %d, want 1", code)
			}
			if want := "the client token is missing or wrong\n"; stderr != want {
				t.Errorf("stderr = %q, want %q", stderr, want)
			}
			if !asJSON {
				if stdout != "" {
					t.Errorf("stdout = %q, want nothing", stdout)
				}
				return
			}
			var got, want any
			if err := json.Unmarshal([]byte(stdout), &got); err != nil {
				t.Fatalf("stdout %q is not JSON: %v", stdout, err)
			}
			json.Unmarshal([]byte(body), &want)
			if !reflect.DeepEqual(got, want) {
				t.Errorf("stdout = %v, want the error body %v", got, want)
			}
		})
	}
}

func TestRun_UsageErrorsSendNoRequest(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		args []string
		// want is a line the usage holds: the command's synopsis, or the
		// list of commands when no command was recognised.
		want string
	}{
		{"no command", nil, "  status "},
		{"unknown command", []string{"frobnicate"}, "  status "},
		{"unknown flag", []string{"status", "--nope"}, "usage: nightshift status"},
		{"extra argument", []string{"status", "extra"}, "usage: nightshift status"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			d := newFakeDaemon(t, http.StatusOK, reachableStatus)
			c := newClient(t)
			c.vars["NIGHTSHIFT_ADDR"] = d.addr()

			code, stdout, stderr := c.run(tt.args...)

			if code != 2 {
				t.Errorf("exit code = %d, want 2", code)
			}
			if !strings.Contains(stderr, "usage: nightshift") || !strings.Contains(stderr, tt.want) {
				t.Errorf("stderr = %q, want a usage holding %q", stderr, tt.want)
			}
			if stdout != "" {
				t.Errorf("stdout = %q, want nothing", stdout)
			}
			if got := d.seen(); len(got) != 0 {
				t.Errorf("the daemon saw %v, want no request", got)
			}
		})
	}
}
