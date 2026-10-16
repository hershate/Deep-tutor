package computer

import (
	"golang.org/x/sys/windows"
	"path/filepath"
)

func processIdentity(pid int32, bundle string) (string, string, error) {
	path, err := processExecutable(pid)
	if err != nil {
		return "", "", err
	}
	exe := filepath.Base(path)
	if bundle == "" || filepath.IsAbs(bundle) {
		bundle = exe
	}
	return bundle, exe, nil
}

func processExecutable(pid int32) (string, error) {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return "", err
	}
	defer windows.CloseHandle(h)
	buf := make([]uint16, 32768)
	n := uint32(len(buf))
	if err := windows.QueryFullProcessImageName(h, 0, &buf[0], &n); err != nil {
		return "", err
	}
	return windows.UTF16ToString(buf[:n]), nil
}
