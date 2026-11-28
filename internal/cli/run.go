// Package cli is the nightshift command-line client of the daemon's API.
package cli

import (
	"fmt"
	"io"
)

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

// Run runs the client with the command-line arguments args (without the
// program name) and returns the process's exit code. It knows no command
// yet, so every invocation is a usage error.
func Run(args []string, env Env) int {
	fmt.Fprintln(env.Stderr, "usage: nightshift <command> [flags]")
	return 2
}
