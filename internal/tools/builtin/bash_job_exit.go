package builtin

import (
	"errors"
	"os/exec"
	"syscall"

	"reasonix/internal/tools/jobs"
)

// jobExit types a background command's failure at the point that knows how the
// process ended. Errors that are not a process's own ending pass through.
func jobExit(err error) error {
	var exitErr *exec.ExitError
	if err == nil || !errors.As(err, &exitErr) || exitErr.ProcessState == nil {
		return err
	}
	typed := &jobs.ExitError{Code: exitErr.ExitCode(), Err: err}
	if ws, ok := exitErr.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
		typed.Signal = int(ws.Signal())
	}
	return typed
}
