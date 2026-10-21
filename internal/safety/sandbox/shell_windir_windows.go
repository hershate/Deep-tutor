package sandbox

import "golang.org/x/sys/windows"

// systemWindowsDir is a seam, not a constant: the WSL launcher is recognised
// by file identity against the real Windows directory, so a test that has to
// prove the recognition needs to point at a directory of its own.
var systemWindowsDir = func() string {
	dir, err := windows.GetSystemWindowsDirectory()
	if err != nil {
		return ""
	}
	return dir
}
