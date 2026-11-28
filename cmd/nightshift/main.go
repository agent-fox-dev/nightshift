// Command nightshift is the client of the nightshift daemon.
package main

import (
	"os"

	"github.com/agent-fox-dev/nightshift/internal/cli"
)

func main() {
	// An unknown home directory is left empty: the client reports it only
	// when a path needs it.
	home, _ := os.UserHomeDir()
	os.Exit(cli.Run(os.Args[1:], cli.Env{Home: home, Getenv: os.Getenv, Stdout: os.Stdout, Stderr: os.Stderr}))
}
