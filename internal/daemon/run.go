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
	"syscall"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/api"
	"github.com/agent-fox-dev/nightshift/internal/config"
	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/store"
)

// shutdownTimeout bounds how long a stop waits for requests in flight.
const shutdownTimeout = 5 * time.Second

// Env is what Run takes from the process, so that tests run the daemon in
// the test process.
type Env struct {
	// Home is the home directory against which a leading "~/" expands and
	// under which the default --config is found.
	Home           string
	Stdout, Stderr io.Writer

	// HubTimeout bounds the hub start check; zero means 10 seconds.
	HubTimeout time.Duration
	// Signals delivers the signals that stop the daemon. When nil, Run
	// takes SIGTERM and SIGINT from the process.
	Signals <-chan os.Signal
	// Started, when set, is called once the API listens, with the running
	// daemon.
	Started func(*Daemon)
}

// Run runs the daemon with the command-line arguments args (without the
// program name) and returns the process's exit code: 0 after a clean stop,
// 1 when a start check fails or the daemon cannot serve, 2 for a usage
// error. Once serving, it runs until SIGTERM or SIGINT.
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
	d := &Daemon{env: env, configPath: *configPath}
	if !d.runStartChecks(context.Background()) {
		return 1
	}

	// Listen for the stop signals before serving, so that none sent once
	// the daemon reports it listens is lost.
	signals := env.Signals
	if signals == nil {
		ch := make(chan os.Signal, 1)
		signal.Notify(ch, syscall.SIGTERM, syscall.SIGINT)
		defer signal.Stop(ch)
		signals = ch
	}

	if err := d.open(); err != nil {
		fmt.Fprintln(env.Stderr, err)
		return 1
	}
	defer d.store.Close()

	srv := &http.Server{Handler: d.api}
	served := make(chan error, 1)
	go func() { served <- srv.Serve(d.ln) }()
	if env.Started != nil {
		env.Started(d)
	}

	waitForStop(signals)

	ctx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		srv.Close()
	}
	<-served
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
	d.store, d.ln = st, ln
	d.api = api.New(token, reachableHub{endpoint: d.hub.Endpoint()}, api.WithErrorLog(d.env.Stderr))
	return nil
}

// waitForStop returns on SIGTERM or SIGINT, or when signals is closed.
func waitForStop(signals <-chan os.Signal) {
	for sig := range signals {
		if sig == syscall.SIGTERM || sig == os.Interrupt {
			return
		}
	}
}

// reachableHub reports the hub as reachable, which the start checks have
// just shown, until the hub monitor takes its place.
type reachableHub struct{ endpoint string }

func (h reachableHub) HubState() api.HubState {
	return api.HubState{Endpoint: h.endpoint, Reachable: true}
}

// Daemon is the state of one run of nightshiftd.
type Daemon struct {
	env        Env
	configPath string
	cfg        config.Config
	hub        *hub.Client
	store      *store.Store
	ln         net.Listener
	api        *api.Server
}

// Addr is the host:port the API listens on.
func (d *Daemon) Addr() string { return d.ln.Addr().String() }

// Hub is the daemon's client of the hub.
func (d *Daemon) Hub() *hub.Client { return d.hub }
