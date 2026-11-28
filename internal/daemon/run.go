// Package daemon runs nightshiftd: it parses the command line, runs the
// start checks and, once they pass, serves the API. internal/daemon/run.go
// is the composition root where every package wires its part.
package daemon

import (
	"errors"
	"flag"
	"fmt"
	"io"

	"github.com/agent-fox-dev/nightshift/internal/config"
)

// Env is what Run takes from the process, so that tests run the daemon in
// the test process.
type Env struct {
	// Home is the home directory against which a leading "~/" expands and
	// under which the default --config is found.
	Home           string
	Stdout, Stderr io.Writer
}

// Run runs the daemon with the command-line arguments args (without the
// program name) and returns the process's exit code: 0 after a clean stop,
// 1 when a start check fails, 2 for a usage error.
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

	// The configuration is checked before anything else is touched: a
	// daemon that starts half-configured fails later with a worse message.
	if _, err := config.Load(*configPath, env.Home); err != nil {
		fmt.Fprintf(env.Stderr, "config: %v\n", err)
		return 1
	}
	return 0
}
