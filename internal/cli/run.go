// Package cli is the nightshift command-line client of the daemon's API.
package cli

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"path/filepath"
	"slices"
	"strings"
	"sync"
)

// DefaultAddr is the address of the daemon when neither --addr nor
// $NIGHTSHIFT_ADDR names one: the daemon's default listen address.
const DefaultAddr = "127.0.0.1:7878"

// Env is what Run takes from the process, so that tests run the client in
// the test process.
type Env struct {
	// Home is the home directory under which the default client token is
	// found.
	Home string
	// Getenv reads an environment variable.
	Getenv         func(string) string
	Stdout, Stderr io.Writer
}

// Command is one command of nightshift.
type Command struct {
	// Name is the word that selects the command.
	Name string
	// Synopsis follows "nightshift " in the command's usage: the name and
	// its arguments, such as "status".
	Synopsis string
	// Summary is the one line that describes the command in nightshift's
	// usage.
	Summary string
	// Setup declares the command's own flags on fs and returns the function
	// that runs the command with its positional arguments once fs is
	// parsed. It is called once per invocation, so the flag values it binds
	// belong to that invocation alone.
	Setup func(fs *flag.FlagSet) func(c *Call, args []string) int
}

var registry struct {
	mu       sync.Mutex
	commands map[string]Command
}

// Register adds a command to nightshift. A package registers its commands
// from an init function; registering a name twice panics.
func Register(c Command) {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	if registry.commands == nil {
		registry.commands = map[string]Command{}
	}
	if _, dup := registry.commands[c.Name]; dup {
		panic("cli: command " + c.Name + " registered twice")
	}
	registry.commands[c.Name] = c
}

func lookup(name string) (Command, bool) {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	c, ok := registry.commands[name]
	return c, ok
}

func commands() []Command {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	out := make([]Command, 0, len(registry.commands))
	for _, c := range registry.commands {
		out = append(out, c)
	}
	slices.SortFunc(out, func(a, b Command) int { return strings.Compare(a.Name, b.Name) })
	return out
}

// globals are the flags every command takes, before or after its name.
type globals struct {
	addr, tokenFile string
	json            bool
}

func (g *globals) define(fs *flag.FlagSet) {
	fs.StringVar(&g.addr, "addr", g.addr, "the daemon's `host:port` (default $NIGHTSHIFT_ADDR, else "+DefaultAddr+")")
	fs.StringVar(&g.tokenFile, "token-file", g.tokenFile, "the client token `file` (default ~/.nightshift/token)")
	fs.BoolVar(&g.json, "json", g.json, "print the daemon's JSON answer")
}

// Run runs the client with the command-line arguments args (without the
// program name) and returns the process's exit code: 0 on success, 1 when
// the command failed, 2 for a usage error. A usage error sends no request.
func Run(args []string, env Env) int {
	var g globals
	top := flag.NewFlagSet("nightshift", flag.ContinueOnError)
	top.SetOutput(env.Stderr)
	g.define(top)
	top.Usage = func() { printUsage(env.Stderr, top) }
	if err := top.Parse(args); err != nil {
		return usageExit(err)
	}
	if top.NArg() == 0 {
		fmt.Fprintln(env.Stderr, "nightshift: no command given")
		top.Usage()
		return 2
	}
	cmd, ok := lookup(top.Arg(0))
	if !ok {
		fmt.Fprintf(env.Stderr, "nightshift: unknown command %q\n", top.Arg(0))
		top.Usage()
		return 2
	}

	fs := flag.NewFlagSet("nightshift "+cmd.Name, flag.ContinueOnError)
	fs.SetOutput(env.Stderr)
	// Defined again with the values parsed so far, so that the global flags
	// are accepted after the command's name too.
	g.define(fs)
	run := cmd.Setup(fs)
	fs.Usage = func() {
		fmt.Fprintf(env.Stderr, "usage: nightshift %s [flags]\n\n%s\n\nflags:\n", cmd.Synopsis, cmd.Summary)
		fs.PrintDefaults()
	}
	if err := fs.Parse(top.Args()[1:]); err != nil {
		return usageExit(err)
	}

	c := &Call{
		Stdout:    env.Stdout,
		Stderr:    env.Stderr,
		JSON:      g.json,
		name:      cmd.Name,
		usage:     fs.Usage,
		addr:      resolveAddr(g.addr, env.Getenv),
		tokenPath: g.tokenFile,
	}
	if c.tokenPath == "" {
		c.tokenPath = filepath.Join(env.Home, ".nightshift", "token")
	}
	return run(c, fs.Args())
}

// usageExit is the exit code of a command line flag could not parse; the
// flag package has printed the error and the usage.
func usageExit(err error) int {
	if errors.Is(err, flag.ErrHelp) {
		return 0
	}
	return 2
}

func resolveAddr(flagAddr string, getenv func(string) string) string {
	if flagAddr != "" {
		return flagAddr
	}
	if getenv != nil {
		if addr := getenv("NIGHTSHIFT_ADDR"); addr != "" {
			return addr
		}
	}
	return DefaultAddr
}

func printUsage(w io.Writer, fs *flag.FlagSet) {
	fmt.Fprintln(w, "usage: nightshift [flags] <command> [arguments]")
	fmt.Fprintln(w, "\ncommands:")
	for _, c := range commands() {
		fmt.Fprintf(w, "  %-12s %s\n", c.Name, c.Summary)
	}
	fmt.Fprintln(w, "\nflags:")
	fs.PrintDefaults()
}
