//go:build darwin || dragonfly || freebsd || netbsd || openbsd

package command

import "syscall"

// awaitExit blocks until the process pid, a child of this one, has exited,
// and leaves it to be reaped: a kqueue's NOTE_EXIT does not reap.
func awaitExit(pid int) error {
	kq, err := syscall.Kqueue()
	if err != nil {
		return err
	}
	defer syscall.Close(kq)
	var ev syscall.Kevent_t
	syscall.SetKevent(&ev, pid, syscall.EVFILT_PROC, syscall.EV_ADD|syscall.EV_ONESHOT)
	ev.Fflags = syscall.NOTE_EXIT
	for {
		_, err = syscall.Kevent(kq, []syscall.Kevent_t{ev}, nil, nil)
		if err != syscall.EINTR {
			break
		}
	}
	if err == syscall.ESRCH {
		// An unreaped child that cannot be watched has exited already.
		return nil
	}
	if err != nil {
		return err
	}
	events := make([]syscall.Kevent_t, 1)
	for {
		n, err := syscall.Kevent(kq, nil, events, nil)
		if err == syscall.EINTR || (err == nil && n == 0) {
			continue
		}
		return err
	}
}
