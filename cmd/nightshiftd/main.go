// Command nightshiftd is the nightshift daemon.
package main

import (
	"os"

	"github.com/agent-fox-dev/nightshift/internal/daemon"
)

func main() {
	// An unknown home directory is left empty: the daemon reports it only
	// when a path needs it.
	home, _ := os.UserHomeDir()
	os.Exit(daemon.Run(os.Args[1:], daemon.Env{Home: home, Stdout: os.Stdout, Stderr: os.Stderr}))
}
