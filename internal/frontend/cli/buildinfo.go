package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"runtime/debug"
	"strings"
)

// BuildInfo is the machine- and human-readable build identity for
// `deep-tutor version` / `deep-tutor --version`. Release builds may only fill
// Version; source and CI builds inject the rest via -ldflags so the binary is
// traceable without shelling out to git.
//
// Plan contract (Integration D/E):
//   - `deep-tutor --version` / `-v` stay single-line: "deep-tutor <version>"
//   - `deep-tutor version --verbose` prints structured fields
//   - `deep-tutor version --json` prints a JSON object
//   - Fields are limited to version, commit, build time, target (and go runtime);
//     no config paths and no fixed CST conversion.
type BuildInfo struct {
	Version      string
	GitCommit    string
	BuildTimeUTC string
	BuildTarget  string
}

// versionCommand handles `deep-tutor version [flags]`. When allowFlags is false
// (top-level --version / -v), output is always the single-line form.
func versionCommand(args []string, info BuildInfo, allowFlags bool) int {
	info = info.withDefaults()
	if !allowFlags {
		fmt.Println(info.singleLine())
		return 0
	}
	verbose := false
	jsonOut := false
	for _, arg := range args {
		switch arg {
		case "--verbose", "-verbose":
			verbose = true
		case "--json":
			jsonOut = true
		case "--help", "-h":
			versionUsage(os.Stdout)
			return 0
		default:
			fmt.Fprintf(os.Stderr, "unknown version flag %q\n", arg)
			versionUsage(os.Stderr)
			return 2
		}
	}
	if jsonOut && verbose {
		fmt.Fprintln(os.Stderr, "error: --json and --verbose are mutually exclusive")
		return 2
	}
	if jsonOut {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(info.jsonPayload()); err != nil {
			fmt.Fprintln(os.Stderr, "error:", err)
			return 1
		}
		return 0
	}
	if verbose {
		fmt.Print(info.verboseText())
		return 0
	}
	fmt.Println(info.singleLine())
	return 0
}

func versionUsage(w *os.File) {
	fmt.Fprintln(w, `Usage:
  deep-tutor version
  deep-tutor version --verbose
  deep-tutor version --json
  deep-tutor --version
  deep-tutor -v

--version / -v always print a single line (deep-tutor <version>).
version --verbose prints build metadata; version --json prints the same as JSON.`)
}

func (b BuildInfo) singleLine() string {
	return "deep-tutor " + strings.TrimSpace(b.withDefaults().Version)
}

func (b BuildInfo) verboseText() string {
	b = b.withDefaults()
	var lines []string
	appendField := func(k, v string) {
		v = strings.TrimSpace(v)
		if v != "" {
			lines = append(lines, k+": "+v)
		}
	}
	lines = append(lines, b.singleLine())
	appendField("git_commit", b.GitCommit)
	appendField("build_time_utc", b.BuildTimeUTC)
	appendField("build_target", b.BuildTarget)
	appendField("go", fmt.Sprintf("%s %s/%s", runtime.Version(), runtime.GOOS, runtime.GOARCH))
	return strings.Join(lines, "\n") + "\n"
}

type versionJSON struct {
	Version      string `json:"version"`
	GitCommit    string `json:"git_commit,omitempty"`
	BuildTimeUTC string `json:"build_time_utc,omitempty"`
	BuildTarget  string `json:"build_target,omitempty"`
	Go           string `json:"go"`
}

func (b BuildInfo) jsonPayload() versionJSON {
	b = b.withDefaults()
	return versionJSON{
		Version:      b.Version,
		GitCommit:    omitUnknown(b.GitCommit),
		BuildTimeUTC: omitUnknown(b.BuildTimeUTC),
		BuildTarget:  b.BuildTarget,
		Go:           fmt.Sprintf("%s %s/%s", runtime.Version(), runtime.GOOS, runtime.GOARCH),
	}
}

func omitUnknown(v string) string {
	v = strings.TrimSpace(v)
	if v == "" || v == "unknown" {
		return ""
	}
	return v
}

func (b BuildInfo) withDefaults() BuildInfo {
	if strings.TrimSpace(b.Version) == "" {
		b.Version = "dev"
	}
	if strings.TrimSpace(b.BuildTarget) == "" {
		b.BuildTarget = defaultBuildTarget()
	}
	// Commit may fall back to the embedded VCS revision from `go build` when
	// ldflags were not set (local `go run` / untagged builds). Build time must
	// NOT use vcs.time — that is commit timestamp, not the binary's build clock.
	// Official release/npm/desktop/Makefile paths inject buildTimeUTC explicitly.
	if strings.TrimSpace(b.GitCommit) == "" {
		if rev, ok := buildSetting("vcs.revision"); ok {
			b.GitCommit = shortGitRevision(rev)
		}
	}
	if strings.TrimSpace(b.GitCommit) == "" {
		b.GitCommit = "unknown"
	}
	if strings.TrimSpace(b.BuildTimeUTC) == "" {
		b.BuildTimeUTC = "unknown"
	}
	return b
}

func buildSetting(key string) (string, bool) {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "", false
	}
	for _, setting := range info.Settings {
		if setting.Key == key && strings.TrimSpace(setting.Value) != "" {
			return setting.Value, true
		}
	}
	return "", false
}

func shortGitRevision(revision string) string {
	revision = strings.TrimSpace(revision)
	if len(revision) > 12 {
		return revision[:12]
	}
	return revision
}

func defaultBuildTarget() string {
	if runtime.GOOS == "" || runtime.GOARCH == "" {
		return ""
	}
	return runtime.GOOS + "/" + runtime.GOARCH
}
