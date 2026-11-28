package command_test

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/command"
)

func TestRun_KeepsTheTailOfStandardOutputAndError(t *testing.T) {
	t.Parallel()
	seq := make([]string, 0, 50)
	for i := 451; i <= 500; i++ {
		seq = append(seq, strconv.Itoa(i))
	}
	dir := t.TempDir()
	tests := []struct {
		name      string
		line      string
		tailLines int
		env       []string
		wantCode  int
		wantTail  []string
	}{
		{name: "both streams in the order written", line: "echo out; echo err >&2; exit 0", wantTail: []string{"out", "err"}},
		{name: "an exit code other than zero is a result", line: "exit 7", wantCode: 7, wantTail: []string{}},
		{name: "only the last lines", line: "seq 1 500", tailLines: 50, wantTail: seq},
		{name: "in the directory", line: "pwd", wantTail: []string{dir}},
		{name: "with the variables added", line: "echo $FOO", env: []string{"FOO=bar"}, wantTail: []string{"bar"}},
		{name: "a long line is cut", line: `printf '%10000s\n' '' | tr ' ' a`, wantTail: []string{strings.Repeat("a", 4096)}},
		{name: "output without a line end is one cut line", line: `head -c 2000000 /dev/zero | tr '\0' b`, wantTail: []string{strings.Repeat("b", 4096)}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			tail := tt.tailLines
			if tail == 0 {
				tail = 10
			}
			var runner command.Runner
			res, err := runner.Run(t.Context(), command.Spec{
				Dir: dir, Line: tt.line, Timeout: 10 * time.Second, TailLines: tail, Env: tt.env,
			})
			if err != nil {
				t.Fatalf("Run: %v", err)
			}
			if res.ExitCode != tt.wantCode || res.TimedOut {
				t.Errorf("exit code %d, timed out %v; want %d, false", res.ExitCode, res.TimedOut, tt.wantCode)
			}
			if !slices.Equal(res.Tail, tt.wantTail) {
				t.Errorf("tail %q, want %q", shorten(res.Tail), shorten(tt.wantTail))
			}
		})
	}
}

func TestRun_KillsTheProcessGroupOnTimeoutOrCancellation(t *testing.T) {
	t.Parallel()
	const line = "echo started; sleep 300 & echo $! > pid; wait"
	tests := []struct {
		name    string
		timeout time.Duration
		cancel  bool
	}{
		{name: "timeout", timeout: 100 * time.Millisecond},
		{name: "cancelled context", timeout: time.Minute, cancel: true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			dir := t.TempDir()
			pidFile := filepath.Join(dir, "pid")
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			if tt.cancel {
				// Cancel once the child has started, and 50ms after the start.
				go func() {
					time.Sleep(50 * time.Millisecond)
					for ctx.Err() == nil {
						if data, _ := os.ReadFile(pidFile); strings.HasSuffix(string(data), "\n") {
							cancel()
							return
						}
						time.Sleep(5 * time.Millisecond)
					}
				}()
			}
			var runner command.Runner
			start := time.Now()
			res, err := runner.Run(ctx, command.Spec{Dir: dir, Line: line, Timeout: tt.timeout, TailLines: 10})
			elapsed := time.Since(start)
			if tt.cancel {
				if !errors.Is(err, context.Canceled) {
					t.Errorf("Run: %v, want the context's error", err)
				}
			} else {
				if err != nil {
					t.Fatalf("Run: %v", err)
				}
				if !res.TimedOut || !slices.Equal(res.Tail, []string{"started"}) {
					t.Errorf("result %+v, want timed out with the tail [started]", res)
				}
			}
			if elapsed >= 5*time.Second {
				t.Errorf("Run returned after %v, want under 5s", elapsed)
			}
			data, err := os.ReadFile(pidFile)
			if err != nil {
				t.Fatalf("the command wrote no pid: %v", err)
			}
			pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
			if err != nil {
				t.Fatalf("pid file %q: %v", data, err)
			}
			// A killed child is gone once its new parent has reaped it.
			deadline := time.Now().Add(3 * time.Second)
			for syscall.Kill(pid, 0) == nil {
				if time.Now().After(deadline) {
					syscall.Kill(pid, syscall.SIGKILL)
					t.Fatalf("the command's child %d is still running", pid)
				}
				time.Sleep(10 * time.Millisecond)
			}
		})
	}
}

func TestRun_RefusesAnUnusableSpecWithoutStartingAProcess(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	file := filepath.Join(dir, "file")
	if err := os.WriteFile(file, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	good := command.Spec{Dir: dir, Line: "touch ran", Timeout: time.Second, TailLines: 1}
	tests := []struct {
		name   string
		change func(*command.Spec)
	}{
		{name: "a directory that does not exist", change: func(s *command.Spec) { s.Dir = filepath.Join(dir, "missing") }},
		{name: "a file for a directory", change: func(s *command.Spec) { s.Dir = file }},
		{name: "an empty line", change: func(s *command.Spec) { s.Line = "" }},
		{name: "no timeout", change: func(s *command.Spec) { s.Timeout = 0 }},
		{name: "no tail", change: func(s *command.Spec) { s.TailLines = 0 }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			spec := good
			tt.change(&spec)
			var runner command.Runner
			res, err := runner.Run(t.Context(), spec)
			if err == nil {
				t.Errorf("Run succeeded, want an error")
			}
			if !reflect.DeepEqual(res, command.Result{}) {
				t.Errorf("result %+v, want the zero result", res)
			}
			if _, err := os.Stat(filepath.Join(dir, "ran")); err == nil {
				t.Errorf("a process ran")
			}
		})
	}
}

func TestRun_AppliesTheIdentityBeforeTheStart(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	spec := func(line string) command.Spec {
		return command.Spec{Dir: dir, Line: line, Timeout: 10 * time.Second, TailLines: 5}
	}

	var runner command.Runner
	calls := 0
	runner.SetIdentity(func(cmd *exec.Cmd) error {
		cmd.Env = append(cmd.Env, "WHO=other")
		calls++
		return nil
	})
	res, err := runner.Run(t.Context(), spec("echo $WHO"))
	if err != nil || !slices.Equal(res.Tail, []string{"other"}) || calls != 1 {
		t.Errorf("with an identity: %+v, %v, %d calls; want the tail [other] and one call", res, err, calls)
	}

	runner.SetIdentity(func(*exec.Cmd) error { return errors.New("no such user") })
	res, err = runner.Run(t.Context(), spec("touch ran"))
	if err == nil || !strings.Contains(err.Error(), "no such user") {
		t.Errorf("with a failing identity: %v, want the identity's error", err)
	}
	if !reflect.DeepEqual(res, command.Result{}) {
		t.Errorf("with a failing identity: %+v, want the zero result", res)
	}
	if _, err := os.Stat(filepath.Join(dir, "ran")); err == nil {
		t.Errorf("with a failing identity a process ran")
	}

	var plain command.Runner
	res, err = plain.Run(t.Context(), spec("id -u"))
	if err != nil || !slices.Equal(res.Tail, []string{strconv.Itoa(os.Getuid())}) {
		t.Errorf("without an identity: %+v, %v; want the daemon's uid %d", res, err, os.Getuid())
	}
}

// shorten keeps a failure message readable when the tail holds long lines.
func shorten(lines []string) []string {
	out := make([]string, len(lines))
	for i, l := range lines {
		if len(l) > 20 {
			l = l[:10] + "…(" + strconv.Itoa(len(l)) + " bytes)"
		}
		out[i] = l
	}
	return out
}
