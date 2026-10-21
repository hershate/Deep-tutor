package cli

import (
	"io"

	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/platform/telemetry"
)

// startTUITelemetry runs the documented first-run consent on the plain terminal,
// before the UI owns the screen, and starts the reporter only once a mode is stored.
func startTUITelemetry(cfg *config.Config, version, profile, permissionMode, sessionMode string, in io.Reader, out, errOut io.Writer) *telemetry.Reporter {
	return startCLITelemetryWithIO(cfg, telemetry.Options{
		Version: version, Interactive: true, CLIMode: "tui", Profile: profile,
		PermissionMode: permissionMode, SessionMode: sessionMode,
	}, in, out, errOut)
}

func tuiSink(inner event.Sink, reporter *telemetry.Reporter) event.Sink {
	return reporter.Wrap(inner)
}
