package command

import (
	"syscall"
	"unsafe"
)

// pPID is waitid's P_PID: wait for the one process named.
const pPID = 1

// awaitExit blocks until the process pid, a child of this one, has exited,
// and leaves it to be reaped: waitid with WNOWAIT, as os/exec itself waits
// before reaping.
func awaitExit(pid int) error {
	var info [16]uint64 // a siginfo_t
	for {
		_, _, errno := syscall.Syscall6(syscall.SYS_WAITID, pPID, uintptr(pid),
			uintptr(unsafe.Pointer(&info)), syscall.WEXITED|syscall.WNOWAIT, 0, 0)
		if errno != syscall.EINTR {
			if errno != 0 {
				return errno
			}
			return nil
		}
	}
}
