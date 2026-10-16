package builtin

import (
	"errors"
	"os/exec"
	"runtime"
	"testing"

	"reasonix/internal/tools/jobs"
)

func TestJobExitTypesAProcessExitCode(t *testing.T) {
	name, args := "sh", []string{"-c", "exit 3"}
	if runtime.GOOS == "windows" {
		name, args = "cmd", []string{"/c", "exit 3"}
	}
	err := jobExit(exec.Command(name, args...).Run())
	var ex *jobs.ExitError
	if !errors.As(err, &ex) || ex.Code != 3 || ex.Signal != 0 {
		t.Fatalf("got %#v, want exit code 3 and no signal", err)
	}
	if err.Error() != "exit status 3" {
		t.Fatalf("the raw text changed: %q", err.Error())
	}
}

func TestJobExitTypesTheSignalThatEndedTheProcess(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("no signals on Windows")
	}
	cmd := exec.Command("sh", "-c", "kill -9 $$")
	err := jobExit(cmd.Run())
	var ex *jobs.ExitError
	if !errors.As(err, &ex) || ex.Signal != 9 {
		t.Fatalf("got %#v, want signal 9", err)
	}
}

func TestJobExitPassesOtherErrorsThrough(t *testing.T) {
	plain := errors.New("could not start")
	if !errors.Is(jobExit(plain), plain) || jobExit(nil) != nil {
		t.Fatal("a non-exit error must pass through untouched")
	}
}
