package daemon

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"sync"
	"syscall"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/config"
)

// The shutdown's deadlines, counted from the stop signal. The steps get 25
// seconds, which leaves 5 to close the store within the 30 a stop may take.
const (
	defaultStepDeadline = 25 * time.Second
	defaultExitDeadline = 30 * time.Second
)

// shutdownStep is a named step a package runs when the daemon stops.
type shutdownStep struct {
	name string
	fn   func(ctx context.Context) error
}

// OnShutdown registers fn as the shutdown step name. On SIGTERM or SIGINT,
// once the API no longer serves, the steps run one after the other in the
// order they were registered, each with a context that is cancelled a
// deadline after the signal; a step still running then is abandoned.
func (d *Daemon) OnShutdown(name string, fn func(ctx context.Context) error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.steps = append(d.steps, shutdownStep{name, fn})
}

// Reload is what SIGHUP does: it loads the configuration again from the
// file the daemon started with and checks its hub tokens as the start check
// credentials does. Tokens that pass are used for every later hub request
// and clear the credential errors. Otherwise the tokens in use stay, and the
// reason is written to stderr as "config: <reason>" or "credentials:
// <reason>" and returned. A reload never stops the daemon, and it does not
// ask the hub's /readyz again.
func (d *Daemon) Reload(ctx context.Context) error {
	d.reloading.Lock()
	defer d.reloading.Unlock()
	cfg, err := config.Load(d.configPath, d.env.Home)
	if err != nil {
		return d.reloadFailed("config", err)
	}
	ctx, cancel := context.WithTimeout(ctx, d.hubTimeout())
	defer cancel()
	worker, approver := cfg.Hub.WorkerToken, cfg.Hub.ApproverToken
	if err := CheckCredentials(ctx, d.hub, worker, approver); err != nil {
		return d.reloadFailed("credentials", err)
	}
	d.hub.SetTokens(worker, approver)
	return nil
}

func (d *Daemon) reloadFailed(check string, err error) error {
	err = fmt.Errorf("%s: %w", check, err)
	fmt.Fprintln(d.stderr, err)
	return err
}

// awaitStop handles the signals until SIGTERM or SIGINT, or until signals is
// closed. A SIGHUP reloads in the background, under ctx, so that a stop
// signal is not held up by a slow hub; reloads joins every reload.
func (d *Daemon) awaitStop(ctx context.Context, signals <-chan os.Signal, reloads *sync.WaitGroup) {
	for sig := range signals {
		switch sig {
		case syscall.SIGHUP:
			reloads.Go(func() { d.Reload(ctx) })
		case syscall.SIGTERM, os.Interrupt:
			return
		}
	}
}

// shutdown stops the daemon after a stop signal received at signalled: it
// ends the event streams, stops the server, runs the shutdown steps under a
// context cancelled at the step deadline, then stops the background loops
// and closes the store. It returns by the exit deadline even when part of
// that has not finished, since a process that does not stop is worse than
// one that stops untidily.
func (d *Daemon) shutdown(signalled time.Time, srv *http.Server, served <-chan error, stopStreams, stopLoops func()) {
	stepCtx, cancel := context.WithDeadline(context.Background(), signalled.Add(orDefault(d.env.StepDeadline, defaultStepDeadline)))
	defer cancel()
	done := make(chan struct{})
	go func() {
		defer close(done)
		// The streams end first, because the server's shutdown waits for
		// every open connection and a stream never ends by itself.
		stopStreams()
		if err := srv.Shutdown(stepCtx); err != nil {
			srv.Close()
		}
		<-served
		d.runShutdownSteps(stepCtx)
		stopLoops()
		if err := d.store.Close(); err != nil {
			fmt.Fprintf(d.stderr, "store: %v\n", err)
		}
	}()
	exit := time.NewTimer(time.Until(signalled.Add(orDefault(d.env.ExitDeadline, defaultExitDeadline))))
	defer exit.Stop()
	select {
	case <-done:
	case <-exit.C:
		fmt.Fprintln(d.stderr, "shutdown: not finished by the exit deadline; exiting")
	}
}

// runShutdownSteps runs the registered steps in order under ctx. A step
// that has not returned when ctx is done is abandoned and named on stderr,
// and the steps after it are not started.
func (d *Daemon) runShutdownSteps(ctx context.Context) {
	d.mu.Lock()
	steps := append([]shutdownStep(nil), d.steps...)
	d.mu.Unlock()
	for i, step := range steps {
		if ctx.Err() != nil {
			for _, rest := range steps[i:] {
				fmt.Fprintf(d.stderr, "shutdown step %q not started: the step deadline passed\n", rest.name)
			}
			return
		}
		result := make(chan error, 1)
		go func() { result <- step.fn(ctx) }()
		select {
		case err := <-result:
			if err != nil {
				fmt.Fprintf(d.stderr, "shutdown step %q: %v\n", step.name, err)
			}
		case <-ctx.Done():
			fmt.Fprintf(d.stderr, "shutdown step %q abandoned\n", step.name)
		}
	}
}

func orDefault(d, def time.Duration) time.Duration {
	if d <= 0 {
		return def
	}
	return d
}

// lockedWriter serialises the writes of the daemon's goroutines to one
// writer, which need not be safe for concurrent use.
type lockedWriter struct {
	mu sync.Mutex
	w  io.Writer
}

func (l *lockedWriter) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.w.Write(p)
}

// hubTimeout bounds the hub requests of a start check or a reload.
func (d *Daemon) hubTimeout() time.Duration {
	return orDefault(d.env.HubTimeout, defaultHubTimeout)
}
