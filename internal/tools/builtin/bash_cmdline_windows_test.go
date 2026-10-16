//go:build windows

package builtin

import (
	"context"
	"encoding/json"
	"errors"
	"os/exec"
	"strings"
	"testing"
	"time"

	"reasonix/internal/contract/tool"
	"reasonix/internal/safety/sandbox"
)

func powerShellCommandAtUnits(t *testing.T, sh sandbox.Shell, units int) string {
	t.Helper()
	command := func(n int) string { return "Write-Output '" + strings.Repeat("中", n) + "'" }
	base, _ := commandLineUnits(unconfinedShellArgv(sh, command(0)))
	if units < base {
		t.Fatalf("target %d is below the empty command's %d units", units, base)
	}
	c := command(units - base)
	if got, _ := commandLineUnits(unconfinedShellArgv(sh, c)); got != units {
		t.Fatalf("command line is %d units, want %d", got, units)
	}
	return c
}

func TestBashRefusesCommandLineOverWindowsLimit(t *testing.T) {
	powershell, err := exec.LookPath("powershell")
	if err != nil {
		t.Skip("powershell not found")
	}
	sh := sandbox.Shell{Kind: sandbox.ShellPowerShell, Path: powershell}
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()

	for _, units := range []int{windowsCommandLineMax - 1, windowsCommandLineMax} {
		fits := powerShellCommandAtUnits(t, sh, units)
		if err := checkCommandLine(unconfinedShellArgv(sh, fits)); err != nil {
			t.Fatalf("preflight refused %d units: %v", units, err)
		}
	}
	args, _ := json.Marshal(map[string]any{"command": "Write-Output '中中中'"})
	res, err := (bash{shell: sh}).ExecuteDetailed(ctx, args)
	if err != nil || !strings.Contains(res.Output, "中中中") {
		t.Fatalf("Unicode command failed: %v; output %.80q", err, res.Output)
	}

	over := powerShellCommandAtUnits(t, sh, windowsCommandLineMax+1)
	args, _ = json.Marshal(map[string]any{"command": over})
	res, err = (bash{shell: sh}).ExecuteDetailed(ctx, args)
	if !errors.Is(err, errCommandLineTooLong) {
		t.Fatalf("command over the limit error = %v, want errCommandLineTooLong", err)
	}
	if res.Execution == nil || res.Execution.State != tool.ShellStateNotRun || res.Execution.MutationRisk != tool.ShellMutationNotStarted {
		t.Fatalf("execution = %+v, want not run and not started", res.Execution)
	}
}
