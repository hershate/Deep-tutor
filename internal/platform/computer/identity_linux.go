package computer

import (
	"fmt"
	"os"
	"path/filepath"
)

func processIdentity(pid int32, bundle string) (string, string, error) {
	path, err := processExecutable(pid)
	if err != nil {
		return "", "", err
	}
	exe := filepath.Base(path)
	if bundle == "" {
		bundle = exe
	}
	return bundle, exe, nil
}

func processExecutable(pid int32) (string, error) {
	return os.Readlink(fmt.Sprintf("/proc/%d/exe", pid))
}
