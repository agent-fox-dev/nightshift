// Package buildinfo holds what the build stamps into the programs.
package buildinfo

// Version is the version of the build. `make build` sets it at link time
// with -ldflags "-X github.com/agent-fox-dev/nightshift/internal/buildinfo.Version=<version>".
var Version = "dev"
