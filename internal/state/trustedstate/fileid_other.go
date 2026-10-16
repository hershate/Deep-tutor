//go:build !linux && !darwin && !freebsd && !netbsd

package trustedstate

import "io/fs"

func fileIdentity(fs.FileInfo) fileID { return fileID{} }
