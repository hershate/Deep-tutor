package cli

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

const (
	// The npm packages that ship a prebuilt CLI binary, and the Homebrew
	// cask that ships an installed one. Both spellings are listed because both
	// exist in the world: the reasonix-* names are the published upstream
	// packages, and a deep-tutor-* install is this build's own. An install
	// that names either of them is a package manager's to replace, and
	// naming only one of them would let the upgrade overwrite the other's
	// files behind its back.
	npmPlatformPkgPrefix    = "@reasonix/cli-"
	npmPlatformPkgPrefixNew = "@deep-tutor/cli-"
	brewCaskName            = "reasonix"
	brewCaskNameNew         = "deep-tutor"
)

var upgradeExecutable = os.Executable

// managedInstallError marks an executable that a package manager owns and keeps
// records for; replacing it behind that manager's back leaves the records
// naming a version that is no longer on disk.
type managedInstallError struct {
	manager string
	command string
}

func (e *managedInstallError) Error() string {
	return "executable is owned by " + e.manager
}

// npmOwnedExecutable reports whether exe is the prebuilt binary of an npm
// package: <pkg>/bin/<exe> with <pkg>/package.json naming one of ours. It
// answers the package's own name, so the message tells the user which
// package to update rather than naming one that may not be installed.
func npmOwnedExecutable(exe string) (string, bool) {
	binDir := filepath.Dir(exe)
	if filepath.Base(binDir) != "bin" {
		return "", false
	}
	raw, err := os.ReadFile(filepath.Join(filepath.Dir(binDir), "package.json"))
	if err != nil {
		return "", false
	}
	var pkg struct {
		Name string `json:"name"`
	}
	if json.Unmarshal(raw, &pkg) != nil {
		return "", false
	}
	if !strings.HasPrefix(pkg.Name, npmPlatformPkgPrefix) &&
		!strings.HasPrefix(pkg.Name, npmPlatformPkgPrefixNew) {
		return "", false
	}
	return pkg.Name, true
}

// brewOwnedExecutable reports whether exe sits in Homebrew's Caskroom/<cask>/
// tree, where `brew upgrade` replaces the whole version directory. It answers
// the cask it found, for the same reason npmOwnedExecutable answers its
// package.
func brewOwnedExecutable(exe string) (string, bool) {
	parts := strings.Split(filepath.ToSlash(exe), "/")
	for i := 0; i+1 < len(parts); i++ {
		if parts[i] != "Caskroom" {
			continue
		}
		switch parts[i+1] {
		case brewCaskName, brewCaskNameNew:
			return parts[i+1], true
		}
	}
	return "", false
}

// checkSelfReplaceable returns a *managedInstallError when the running
// executable belongs to a package manager.
func checkSelfReplaceable() error {
	exe, err := upgradeExecutable()
	if err != nil {
		return nil
	}
	resolved, _ := resolveSymlinks(exe)
	if pkg, ok := npmOwnedExecutable(resolved); ok {
		return &managedInstallError{manager: "npm", command: "npm install -g " + npmGlobalName(pkg) + "@latest"}
	}
	if cask, ok := brewOwnedExecutable(resolved); ok {
		return &managedInstallError{manager: "Homebrew", command: "brew upgrade --cask " + cask}
	}
	return nil
}

// npmGlobalName is the name `npm install -g` takes for this package family:
// the scope's own package, without the platform suffix, because npm installs
// every platform package of a scope under the one name.
func npmGlobalName(pkg string) string {
	switch {
	case strings.HasPrefix(pkg, npmPlatformPkgPrefixNew):
		return "deep-tutor"
	case strings.HasPrefix(pkg, npmPlatformPkgPrefix):
		return "reasonix"
	default:
		return pkg
	}
}
