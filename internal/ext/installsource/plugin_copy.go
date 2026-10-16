package installsource

import (
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"

	"reasonix/internal/ext/pluginpkg"
)

// siblingPublication is true where a directory held open by another process
// cannot be renamed, so a replacement is published beside the old tree.
var siblingPublication = runtime.GOOS == "windows"

func installPluginCopy(pkg pluginpkg.Package, sourceRoot, target string, replace bool, approvedRoot, expected string) (string, func(), error) {
	if !replace || !siblingPublication {
		if err := installCopiedPlugin(pkg, sourceRoot, target, replace, approvedRoot, expected); err != nil {
			return "", nil, err
		}
		if replace {
			return target, func() {}, nil
		}
		return target, func() { _ = os.RemoveAll(target) }, nil
	}
	// A sibling keeps the old tree intact through content validation and pointer
	// publication, including Windows watches that deny ancestor renames.
	root, err := stagePluginCopy(pkg, sourceRoot, target, approvedRoot, expected)
	if err != nil {
		return "", nil, err
	}
	return root, func() { _ = os.RemoveAll(root) }, nil
}

func stagePluginCopy(pkg pluginpkg.Package, sourceRoot, target, approvedRoot string, expected ...string) (string, error) {
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return "", err
	}
	staging, err := os.MkdirTemp(filepath.Dir(target), "."+filepath.Base(target)+".staging-")
	if err != nil {
		return "", err
	}
	complete := false
	defer func() {
		if !complete {
			_ = os.RemoveAll(staging)
		}
	}()
	if err := copyDir(sourceRoot, staging, tarballTotalLimit, approvedRoot); err != nil {
		return "", err
	}
	// Copying a symlink must not silently remove approved capabilities.
	if err := verifyCopiedCapabilities(pkg, staging); err != nil {
		return "", err
	}
	if len(expected) != 0 {
		if err := verifyPluginDigest(staging, expected[0]); err != nil {
			return "", err
		}
	}
	if err := os.Chmod(staging, 0o755); err != nil {
		return "", err
	}
	if err := syncPluginCopy(staging); err != nil {
		return "", err
	}
	complete = true
	return staging, nil
}

func syncPluginCopy(root string) error {
	var dirs []string
	if err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			dirs = append(dirs, path)
			return nil
		}
		f, err := os.OpenFile(path, os.O_RDWR, 0)
		if err != nil {
			return err
		}
		syncErr := f.Sync()
		closeErr := f.Close()
		if syncErr != nil {
			return syncErr
		}
		return closeErr
	}); err != nil {
		return err
	}
	// Directory fsync is unsupported on Windows; the registry remains the sole
	// restart authority even when directory-entry durability is unavailable.
	for _, dir := range slices.Backward(dirs) {
		if f, err := os.Open(dir); err == nil {
			_ = f.Sync()
			_ = f.Close()
		}
	}
	return nil
}

// A replacement published beside the old tree leaves that tree, and any earlier
// replacement, on disk so a watched directory cannot block it. Removing the
// plugin is when those are reclaimed; one that cannot be removed now is left
// for the next removal and does not fail an uninstall the registry already
// recorded.
func removeSupersededPluginCopies(reasonixHome, pluginsDir, name string) {
	_ = os.RemoveAll(filepath.Join(pluginsDir, name))
	entries, err := os.ReadDir(pluginsDir)
	if err != nil {
		return
	}
	// Another plugin's name may share the prefix, so a directory the registry
	// still points at is never touched.
	inUse := map[string]bool{}
	if st, err := pluginpkg.LoadState(reasonixHome); err == nil {
		for _, p := range st.Plugins {
			inUse[filepath.Clean(pluginpkg.ResolveRoot(reasonixHome, p.Root))] = true
		}
	}
	prefix := "." + name + ".staging-"
	for _, entry := range entries {
		path := filepath.Join(pluginsDir, entry.Name())
		if entry.IsDir() && strings.HasPrefix(entry.Name(), prefix) && !inUse[filepath.Clean(path)] {
			_ = os.RemoveAll(path)
		}
	}
}
