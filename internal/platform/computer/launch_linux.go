package computer

import (
	"debug/elf"
	"path/filepath"
	"reasonix/internal/base/shellparse"
	"strings"
	"unicode"
)

func nativeLaunchSpecification(app nativeApplication) (launchSpecification, error) {
	spec := launchSpecification{identity: app.Bundle}
	command, err := shellparse.ParseStaticCommand(app.LaunchPath, shellparse.StaticCommandPolicy{})
	if err != nil || len(command.Argv) != 1 || !safeNativeLaunchPath(command.Argv[0]) || app.Bundle == "" {
		return spec, fail(CodeUnsupported, "launch requires a single absolute executable from the native catalogue without arguments")
	}
	spec.executable = command.Argv[0]
	spec.args = map[string]any{"launch_path": spec.executable}
	return spec, nil
}

func sameExecutablePath(actual, expected string) bool { return actual == expected }

func safeNativeLaunchPath(file string) bool {
	if !filepath.IsAbs(file) {
		return false
	}
	for _, r := range file {
		if !unicode.IsLetter(r) && !unicode.IsDigit(r) && !strings.ContainsRune("/._-", r) {
			return false
		}
	}
	return true
}

func attestNativeLaunchBinary(file string) error {
	if !safeNativeLaunchPath(file) {
		return fail(CodeUnsupported, "launch executable cannot contain command syntax")
	}
	binary, err := elf.Open(file)
	if err != nil {
		return fail(CodeUnsupported, "launch requires a native ELF executable, not a script")
	}
	defer binary.Close()
	if binary.Type != elf.ET_EXEC && binary.Type != elf.ET_DYN {
		return fail(CodeUnsupported, "launch target is not an executable ELF image")
	}
	return nil
}
