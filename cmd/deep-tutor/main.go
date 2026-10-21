// Command deep-tutor is a config- and plugin-driven coding agent CLI.
package main

import (
	"os"
	"runtime/debug"

	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/provider"
	"deep-tutor/internal/frontend/cli"
	"deep-tutor/internal/platform/crashreport"

	// Blank imports wire compile-time built-ins into their registries.
	_ "deep-tutor/internal/model/anthropic"
	_ "deep-tutor/internal/model/openai"
	_ "deep-tutor/internal/model/responses"
	_ "deep-tutor/internal/tools/builtin"
)

// Build identity injected via -ldflags (see Makefile). version remains the
// single-line contract for `deep-tutor --version`; gitCommit/buildTimeUTC feed
// `deep-tutor version --verbose` / `--json` without embedding config paths.
var (
	version      = "dev"
	gitCommit    = ""
	buildTimeUTC = ""
)

// runCLI is the CLI entry; tests may stub it. Production routes through
// RunWithBuildInfo so ldflags metadata is available to version --verbose/--json.
var runCLI = func(args []string, buildVersion string) int {
	return cli.RunWithBuildInfo(args, cli.BuildInfo{
		Version:      buildVersion,
		GitCommit:    gitCommit,
		BuildTimeUTC: buildTimeUTC,
	})
}

func main() {
	provider.SetClientVersion(version)
	os.Exit(runWithCrashCapture(os.Args[1:], version))
}

func runWithCrashCapture(args []string, buildVersion string) (exitCode int) {
	home := config.ReasonixHomeDir()
	crashreport.CaptureFatalDumps(home, buildVersion)
	releaseFatalOutput := crashreport.InstallFatalOutput(home)
	defer func() {
		// A panic recovered here is already reported, so the runtime's copy of
		// the re-raise must not queue a second one.
		releaseFatalOutput()
		if recovered := recover(); recovered != nil {
			_ = crashreport.CapturePanic(home, buildVersion, recovered, debug.Stack())
			panic(recovered)
		}
	}()
	return runCLI(args, buildVersion)
}
