//go:build !windows

package config

import (
	"os"
	"syscall"
)

func ownedByCurrentUser(info os.FileInfo) bool {
	st, ok := info.Sys().(*syscall.Stat_t)
	return !ok || int(st.Uid) == os.Getuid()
}

func isReparsePoint(string) bool { return false }
