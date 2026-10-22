// Command canary asks a test run one question: did it write into the home the
// product resolves? The answer is how a suite stops damaging the machine it
// runs on: a test that closes a process-wide catalog writes into the user's
// own cache without saying so.
//
// The check has to be dynamic: a package that never mentions a home variable
// still reaches one through a path helper, so only a run against a disposable
// home shows the write.
package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"

	"deep-tutor/internal/base/envvar"
	"deep-tutor/internal/base/scratchroot"
)

// homeVars are the two spellings of the home variable. Both are set for the
// child: the current one wins the lookup, and the old one is what an older
// build still reads.
var homeVars = []string{"DEEPTUTOR_HOME", "REASONIX_HOME"}

// redirectedVars are the variables that pin a location besides the home. A
// machine that carries one would send the suite's writes somewhere the canary
// does not hold, and the check would pass without having looked.
var redirectedVars = []string{
	"DEEPTUTOR_STATE_HOME",
	"DEEPTUTOR_CACHE_HOME",
	"DEEPTUTOR_WORKSPACE_ROOT",
	"REASONIX_STATE_HOME",
	"REASONIX_CACHE_HOME",
	"REASONIX_WORKSPACE_ROOT",
}

func main() {
	if err := dispatch(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "canary:", err)
		os.Exit(1)
	}
}

func dispatch(args []string) error {
	if len(args) == 0 {
		return errors.New("usage: canary run [--home <dir>] -- <go test args...> | canary check <dir>")
	}
	switch args[0] {
	case "run":
		return runSuite(args[1:])
	case "check":
		return checkHome(args[1:])
	default:
		return fmt.Errorf("unknown subcommand %q", args[0])
	}
}

// runSuite points the suite at a disposable home, runs it, and reports what
// the suite left behind.
func runSuite(args []string) error {
	home, testArgs, err := parseRunArgs(args)
	if err != nil {
		return err
	}
	if home == "" {
		if home, err = freshHome(); err != nil {
			return err
		}
	}
	if err := usableHome(home); err != nil {
		return err
	}
	code, err := goTest(testArgs, home)
	if err != nil {
		return err
	}
	var problems []error
	if left := leaked(home); len(left) > 0 {
		problems = append(problems, fmt.Errorf("the suite wrote into the home at %s:\n  %s",
			home, strings.Join(left, "\n  ")))
	}
	if code != 0 {
		problems = append(problems, fmt.Errorf("the test run failed with exit code %d", code))
	}
	return errors.Join(problems...)
}

// checkHome reports a home that holds something. It is the half of the tool
// that needs no test run: a caller can point a suite at a canary home with
// the environment and then ask this.
func checkHome(args []string) error {
	if len(args) != 1 {
		return errors.New("usage: canary check <dir>")
	}
	left := leaked(args[0])
	if len(left) == 0 {
		return nil
	}
	return fmt.Errorf("the suite wrote into the home at %s:\n  %s", args[0], strings.Join(left, "\n  "))
}

// goTest runs the suite under the canary home and reports its exit code.
func goTest(args []string, home string) (int, error) {
	cmd := exec.Command("go", append([]string{"test"}, args...)...)
	cmd.Env = canaryEnv(home)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exit.ExitCode(), nil
		}
		return 0, fmt.Errorf("run go test: %w", err)
	}
	return 0, nil
}

// canaryEnv is the environment the suite runs under. Every variable that pins
// a location is replaced rather than added to: the first entry of a name wins,
// so a second entry for a variable the machine already carries would leave the
// suite reading the machine's home and the canary would see nothing.
func canaryEnv(home string) []string {
	drop := map[string]bool{}
	for _, name := range homeVars {
		drop[name] = true
	}
	for _, name := range redirectedVars {
		drop[name] = true
	}
	env := make([]string, 0, len(os.Environ())+len(homeVars))
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		if drop[name] {
			continue
		}
		env = append(env, entry)
	}
	for _, name := range homeVars {
		env = append(env, name+"="+home)
	}
	return env
}

// freshHome creates the disposable home under the scratch area, so a run
// leaves nothing on the system volume and nothing in the repository.
func freshHome() (string, error) {
	root, err := scratchroot.Ensure()
	if err != nil {
		return "", err
	}
	dir, err := os.MkdirTemp(root, "canary-home-*")
	if err != nil {
		return "", fmt.Errorf("create canary home: %w", err)
	}
	return dir, nil
}

// usableHome refuses a home the run cannot judge. A home that already holds
// files would report them as the suite's, and a home that is the one the
// product resolves would turn the check into a report on the user's own
// files — the opposite of what it is for.
func usableHome(home string) error {
	abs, err := filepath.Abs(home)
	if err != nil {
		return fmt.Errorf("resolve %s: %w", home, err)
	}
	for _, name := range homeVars {
		set, ok := envvar.Lookup(name)
		if ok && set != "" && samePath(set, abs) {
			return fmt.Errorf("%s already names %s; the canary home must not be the home the product resolves", name, abs)
		}
	}
	entries, err := os.ReadDir(abs)
	if err != nil {
		return fmt.Errorf("read %s: %w", abs, err)
	}
	if len(entries) > 0 {
		return fmt.Errorf("%s already holds %d entries; point the canary at an empty directory", abs, len(entries))
	}
	return nil
}

// leaked lists what the suite left in the home, relative to it.
func leaked(home string) []string {
	var out []string
	_ = filepath.WalkDir(home, func(path string, entry os.DirEntry, err error) error {
		if err != nil || path == home {
			return nil
		}
		rel, relErr := filepath.Rel(home, path)
		if relErr != nil {
			return nil
		}
		name := filepath.ToSlash(rel)
		if entry.IsDir() {
			name += "/"
		}
		out = append(out, name)
		return nil
	})
	slices.Sort(out)
	return out
}

// parseRunArgs reads the options and hands back the arguments for go test.
// The separator is what keeps a flag for this tool from being read as one for
// go test.
func parseRunArgs(args []string) (string, []string, error) {
	home := ""
	for len(args) > 0 {
		switch args[0] {
		case "--home":
			if len(args) < 2 {
				return "", nil, errors.New("--home needs a directory")
			}
			home, args = args[1], args[2:]
		case "--":
			return home, args[1:], nil
		default:
			return "", nil, fmt.Errorf("unexpected argument %q", args[0])
		}
	}
	return "", nil, errors.New("usage: canary run [--home <dir>] -- <go test args...>")
}

// samePath compares two paths the way the operating system does: cleaned,
// and without regard to case where the filesystem has none.
func samePath(a, b string) bool {
	ca, cb := filepath.Clean(a), filepath.Clean(b)
	if runtime.GOOS == "windows" {
		return strings.EqualFold(ca, cb)
	}
	return ca == cb
}
