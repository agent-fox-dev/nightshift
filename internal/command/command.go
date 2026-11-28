// Package command runs a shell command line in a directory with a timeout,
// keeping the tail of its output. The health command, the gate and the
// sessions' commands all run foreign code in a checkout through it, so it
// bounds the time a command takes, the memory its output takes and the
// user it runs as.
package command

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

// MaxLineBytes is the length a kept line of output is cut at.
const MaxLineBytes = 4096

// waitDelay is how long Run waits, once the command has ended, for the
// processes it started to let go of its output pipe.
const waitDelay = 2 * time.Second

// Spec is one command to run.
type Spec struct {
	// Dir is the directory the command runs in; it must exist.
	Dir string
	// Line is the command line, run with sh -c.
	Line string
	// Timeout is how long the command may run; greater than zero.
	Timeout time.Duration
	// TailLines is how many of the last lines of output are kept; greater
	// than zero.
	TailLines int
	// Env holds KEY=value entries added to the daemon's environment.
	Env []string
}

// Result is how a command that ran ended.
type Result struct {
	// ExitCode is the command's exit status; 128 plus the signal's number
	// for a command a signal ended.
	ExitCode int
	// TimedOut reports that the command was killed at its timeout.
	TimedOut bool
	// Tail is the last lines of standard output and standard error
	// together, in the order written, without line ends, each cut at
	// MaxLineBytes.
	Tail []string
}

// Identity prepares a command to run as another operating-system user, for
// example by setting cmd.SysProcAttr.Credential. It is called with the
// command's Dir, Env and SysProcAttr set, before the command starts; an
// error keeps it from starting.
type Identity func(cmd *exec.Cmd) error

// Runner runs commands. The zero value is ready for use and runs them as
// the daemon's user; it is safe for concurrent use.
type Runner struct {
	mu       sync.Mutex
	identity Identity
	// beforeSignal, when set, is called with the group's id each time Run
	// is about to signal a command's process group; a test watches it.
	beforeSignal func(pgid int)
}

// SetIdentity applies id to every process Run starts from now on; nil
// runs them as the daemon's user again.
func (r *Runner) SetIdentity(id Identity) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.identity = id
}

// Run runs spec.Line with sh -c in spec.Dir and waits for it. A command
// that ran is a Result and a nil error, whatever its exit code. At the
// timeout the command's process group is killed and the Result says
// TimedOut; when ctx is cancelled the group is killed too and ctx's error
// is returned. Once the shell has exited, and before it is reaped, the
// group is killed whatever the way it ended, so that nothing the command
// left behind runs on. A spec that cannot run, a process that cannot
// start, or a system where Run cannot wait for the shell without reaping
// it (other than Linux, macOS and the BSDs) is an error with the zero
// Result.
func (r *Runner) Run(ctx context.Context, spec Spec) (Result, error) {
	if err := validate(spec); err != nil {
		return Result{}, err
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	dir, err := filepath.Abs(spec.Dir)
	if err != nil {
		return Result{}, fmt.Errorf("command: directory %s: %w", spec.Dir, err)
	}

	cmd := exec.Command("sh", "-c", spec.Line)
	cmd.Dir = dir
	// PWD is the directory, as os/exec sets it for a command with no Env of
	// its own, so that the shell's pwd names Dir as given.
	cmd.Env = append(append(os.Environ(), "PWD="+dir), spec.Env...)
	// A group of its own lets a timeout kill every process the command
	// started, not only the shell.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	r.mu.Lock()
	identity := r.identity
	r.mu.Unlock()
	if identity != nil {
		if err := identity(cmd); err != nil {
			return Result{}, fmt.Errorf("command: identity: %w", err)
		}
		if cmd.SysProcAttr == nil {
			cmd.SysProcAttr = &syscall.SysProcAttr{}
		}
		cmd.SysProcAttr.Setpgid = true
	}

	// One pipe for both streams keeps their lines in the order written.
	pr, pw, err := os.Pipe()
	if err != nil {
		return Result{}, fmt.Errorf("command: %w", err)
	}
	cmd.Stdout, cmd.Stderr = pw, pw
	if err := cmd.Start(); err != nil {
		pr.Close()
		pw.Close()
		return Result{}, fmt.Errorf("command: cannot start sh: %w", err)
	}
	// The command holds the write end now; with the parent's closed, the
	// reader sees the end of output once every process of the command has
	// closed its own.
	pw.Close()
	tail := newRing(spec.TailLines)
	read := make(chan struct{})
	go func() {
		defer close(read)
		readLines(pr, tail)
	}()
	// The shell is reaped only once its group has been signalled: until
	// then its process id, which is the group's, can name no other group.
	exited := make(chan error, 1)
	go func() { exited <- awaitExit(cmd.Process.Pid) }()

	timer := time.NewTimer(spec.Timeout)
	defer timer.Stop()
	var timedOut bool
	var awaitErr, ctxErr error
	select {
	case awaitErr = <-exited:
	case <-timer.C:
		timedOut = true
		r.killGroup(cmd)
		awaitErr = <-exited
	case <-ctx.Done():
		ctxErr = ctx.Err()
		r.killGroup(cmd)
		awaitErr = <-exited
	}
	// The processes the command left behind end with it, so that none runs
	// on in the checkout or holds the pipe.
	r.killGroup(cmd)
	waitErr := cmd.Wait()
	select {
	case <-read:
	case <-time.After(waitDelay):
		// A process that left the group still holds the pipe; stop reading.
		pr.Close()
		<-read
	}
	pr.Close()

	if awaitErr != nil {
		return Result{}, fmt.Errorf("command: cannot wait for sh: %w", awaitErr)
	}
	res := Result{TimedOut: timedOut, Tail: tail.lines()}
	if ctxErr != nil {
		return res, ctxErr
	}
	var exitErr *exec.ExitError
	if waitErr != nil && !errors.As(waitErr, &exitErr) {
		return Result{}, fmt.Errorf("command: %w", waitErr)
	}
	res.ExitCode = exitCode(cmd.ProcessState)
	return res, nil
}

func validate(spec Spec) error {
	if spec.Line == "" {
		return errors.New("command: the command line is empty")
	}
	if spec.Timeout <= 0 {
		return fmt.Errorf("command: the timeout %v is not greater than zero", spec.Timeout)
	}
	if spec.TailLines <= 0 {
		return fmt.Errorf("command: the tail of %d lines is not greater than zero", spec.TailLines)
	}
	info, err := os.Stat(spec.Dir)
	if err != nil {
		return fmt.Errorf("command: directory: %w", err)
	}
	if !info.IsDir() {
		return fmt.Errorf("command: %s is not a directory", spec.Dir)
	}
	return nil
}

// killGroup kills the command's process group. The group's id is the
// shell's process id, which no other group can take while the shell is
// not reaped.
func (r *Runner) killGroup(cmd *exec.Cmd) {
	if r.beforeSignal != nil {
		r.beforeSignal(cmd.Process.Pid)
	}
	syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
}

func exitCode(state *os.ProcessState) int {
	if ws, ok := state.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
		return 128 + int(ws.Signal())
	}
	return state.ExitCode()
}

// readLines reads r to its end into tail, cutting each line at
// MaxLineBytes as it reads, so that memory stays bounded for output of any
// size, a line without an end included.
func readLines(r io.Reader, tail *ring) {
	br := bufio.NewReaderSize(r, MaxLineBytes)
	line := make([]byte, 0, MaxLineBytes)
	pending := false // line holds bytes of a line not yet ended
	for {
		chunk, err := br.ReadSlice('\n')
		if len(chunk) > 0 {
			pending = true
			ended := chunk[len(chunk)-1] == '\n'
			if ended {
				chunk = chunk[:len(chunk)-1]
			}
			if room := MaxLineBytes - len(line); len(chunk) > room {
				chunk = chunk[:room]
			}
			line = append(line, chunk...)
			if ended {
				tail.add(string(line))
				line, pending = line[:0], false
			}
		}
		if err == bufio.ErrBufferFull {
			continue
		}
		if err != nil {
			if pending {
				tail.add(string(line))
			}
			return
		}
	}
}

// ring keeps the last size lines added to it. It grows as lines come, so
// that a large size costs nothing for a command that writes little.
type ring struct {
	buf   []string
	size  int
	start int // the oldest line once the ring is full
}

func newRing(size int) *ring { return &ring{size: size} }

func (r *ring) add(line string) {
	if len(r.buf) < r.size {
		r.buf = append(r.buf, line)
		return
	}
	r.buf[r.start] = line
	r.start = (r.start + 1) % r.size
}

// lines is the kept lines, oldest first; never nil.
func (r *ring) lines() []string {
	out := make([]string, 0, len(r.buf))
	out = append(out, r.buf[r.start:]...)
	return append(out, r.buf[:r.start]...)
}
