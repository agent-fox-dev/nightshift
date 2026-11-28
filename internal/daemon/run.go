// Package daemon runs nightshiftd: it parses the command line, runs the
// start checks and, once they pass, serves the API. internal/daemon/run.go
// is the composition root where every package wires its part.
package daemon

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/api"
	"github.com/agent-fox-dev/nightshift/internal/config"
	"github.com/agent-fox-dev/nightshift/internal/event"
	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/store"
)

// pruneInterval is how often the event log drops the events older than
// its retention, after the prune at start, unless Env.PruneInterval says
// otherwise.
const pruneInterval = time.Hour

// Env is what Run takes from the process, so that tests run the daemon in
// the test process.
type Env struct {
	// Home is the home directory against which a leading "~/" expands and
	// under which the default --config is found.
	Home           string
	Stdout, Stderr io.Writer

	// HubTimeout bounds the hub start check; zero means 10 seconds.
	HubTimeout time.Duration
	// Signals delivers the signals: SIGTERM and SIGINT stop the daemon,
	// SIGHUP reloads its hub tokens. When nil, Run takes them from the
	// process.
	Signals <-chan os.Signal
	// Started, when set, is called once the API listens, with the running
	// daemon.
	Started func(*Daemon)
	// Clock gives the time of events and of the event log's prune; nil
	// means time.Now.
	Clock func() time.Time
	// ProbeInterval is how often /readyz is asked while the hub is
	// unreachable; zero means 30 seconds.
	ProbeInterval time.Duration
	// PruneInterval is how often the event log is pruned after the prune
	// at start; zero means an hour.
	PruneInterval time.Duration
	// StepDeadline is how long after a stop signal the shutdown steps'
	// context is cancelled; zero means 25 seconds.
	StepDeadline time.Duration
	// ExitDeadline is how long after a stop signal Run returns at the
	// latest; zero means 30 seconds.
	ExitDeadline time.Duration
}

// Run runs the daemon with the command-line arguments args (without the
// program name) and returns the process's exit code: 0 after a clean stop,
// 1 when a start check fails or the daemon cannot serve, 2 for a usage
// error. Once serving, it runs until SIGTERM or SIGINT, reloading its hub
// tokens on SIGHUP, and rides out an outage of the hub.
func Run(args []string, env Env) int {
	fs := flag.NewFlagSet("nightshiftd", flag.ContinueOnError)
	fs.SetOutput(env.Stderr)
	configPath := fs.String("config", "", "the configuration `file` (default ~/.nightshift/config.toml)")
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if fs.NArg() > 0 {
		fmt.Fprintf(env.Stderr, "nightshiftd: unexpected argument %q\n", fs.Arg(0))
		fs.Usage()
		return 2
	}

	// The checks come before anything else is touched: a daemon that starts
	// half-configured fails later with a worse message.
	d := &Daemon{env: env, configPath: *configPath, stderr: &lockedWriter{w: env.Stderr}}
	if !d.runStartChecks(context.Background()) {
		return 1
	}

	// Listen for the signals before serving, so that none sent once the
	// daemon reports it listens is lost, and a SIGHUP does not end the
	// process as it does by default.
	signals := env.Signals
	if signals == nil {
		ch := make(chan os.Signal, 1)
		signal.Notify(ch, syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP)
		defer signal.Stop(ch)
		signals = ch
	}

	if err := d.open(); err != nil {
		fmt.Fprintln(env.Stderr, err)
		return 1
	}

	// The event log is pruned before the API serves, then hourly; the hub
	// monitor probes while the hub is unreachable. Both loops, and any
	// reload, run until the shutdown stops them, before the store closes.
	d.prune(context.Background())
	loops, stopLoops := context.WithCancel(context.Background())
	var background sync.WaitGroup
	background.Go(func() { d.pruneLoop(loops, orDefault(env.PruneInterval, pruneInterval)) })
	background.Go(func() { d.hub.Monitor().Run(loops) })

	streams, stopStreams := context.WithCancel(context.Background())
	defer stopStreams()
	d.api.HandleEvents(streams, d.log)

	srv := &http.Server{Handler: d.api}
	served := make(chan error, 1)
	go func() { served <- srv.Serve(d.ln) }()
	if env.Started != nil {
		env.Started(d)
	}

	d.awaitStop(loops, signals, &background)
	d.shutdown(time.Now(), srv, served, stopStreams, func() {
		stopLoops()
		background.Wait()
	})
	return 0
}

// open opens the store, ensures the client token and listens, in that
// order. Its error is the line Run prints, named after the step that
// failed; on an error nothing stays open.
func (d *Daemon) open() error {
	st, err := store.Open(d.cfg.Store.Path)
	if err != nil {
		return fmt.Errorf("store: %w", err)
	}
	log, err := event.Open(st, d.clock())
	if err != nil {
		st.Close()
		return fmt.Errorf("store: %w", err)
	}
	token, err := api.EnsureClientToken(d.cfg.TokenFile)
	if err != nil {
		st.Close()
		return fmt.Errorf("token: %w", err)
	}
	ln, err := net.Listen("tcp", d.cfg.Listen)
	if err != nil {
		st.Close()
		return fmt.Errorf("listen: %w", err)
	}
	d.store, d.log, d.ln = st, log, ln
	d.api = api.New(token, api.HubReporterFunc(d.hubState), api.WithErrorLog(d.stderr))
	return nil
}

// clock is the time source of the events and of the hub's first failure.
func (d *Daemon) clock() func() time.Time {
	if d.env.Clock == nil {
		return time.Now
	}
	return d.env.Clock
}

// hubState is what the status reports about the hub, from the monitor.
func (d *Daemon) hubState() api.HubState {
	s := d.hub.Monitor().State()
	return api.HubState{
		Endpoint:         d.hub.Endpoint(),
		Reachable:        s.Reachable,
		UnreachableSince: s.UnreachableSince,
		CredentialError:  s.CredentialError,
	}
}

// prune drops the event log's expired events; a failure is reported and
// the daemon goes on, since the events are only kept longer.
func (d *Daemon) prune(ctx context.Context) {
	if err := d.log.Prune(ctx); err != nil && ctx.Err() == nil {
		fmt.Fprintln(d.stderr, err)
	}
}

// pruneLoop prunes the event log every interval until ctx is done.
func (d *Daemon) pruneLoop(ctx context.Context, interval time.Duration) {
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			d.prune(ctx)
		}
	}
}

// Daemon is the state of one run of nightshiftd.
type Daemon struct {
	env        Env
	configPath string
	cfg        config.Config
	hub        *hub.Client
	store      *store.Store
	log        *event.Log
	ln         net.Listener
	api        *api.Server
	// stderr is env.Stderr, safe for the daemon's goroutines to share.
	stderr io.Writer

	// reloading makes reloads run one at a time.
	reloading sync.Mutex
	// mu guards steps.
	mu    sync.Mutex
	steps []shutdownStep
}

// Addr is the host:port the API listens on.
func (d *Daemon) Addr() string { return d.ln.Addr().String() }

// Hub is the daemon's client of the hub.
func (d *Daemon) Hub() *hub.Client { return d.hub }

// Log is the daemon's event log, which other packages append to.
func (d *Daemon) Log() *event.Log { return d.log }
