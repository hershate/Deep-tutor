package wsl

import "golang.org/x/sys/windows"

// systemDirectory is the Windows system directory the OS reports, not the one an
// environment variable names, so a changed environment cannot stand in for it.
func systemDirectory() string {
	dir, err := windows.GetSystemDirectory()
	if err != nil {
		return ""
	}
	return dir
}
