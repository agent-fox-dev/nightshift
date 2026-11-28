//go:build !linux && !darwin && !dragonfly && !freebsd && !netbsd && !openbsd

package command

import "errors"

// awaitExit has no way here to wait for a process without reaping it.
func awaitExit(int) error { return errors.ErrUnsupported }
