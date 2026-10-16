//go:build linux

package trustedstate

import (
	"io/fs"
	"syscall"
)

func fileIdentity(info fs.FileInfo) fileID {
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return fileID{}
	}
	return fileID{dev: uint64(st.Dev), ino: st.Ino, ctimeNs: st.Ctim.Nano()}
}
