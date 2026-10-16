package config

import (
	"io/fs"
	"os"
)

func ownedByCurrentUser(os.FileInfo) bool { return true }

// isReparsePoint reports a junction or link at path itself: filepath.EvalSymlinks
// does not follow Windows mount points, so the resolved path would still name
// the junction while writes land in its target.
func isReparsePoint(path string) bool {
	info, err := os.Lstat(path)
	return err != nil || info.Mode()&(fs.ModeSymlink|fs.ModeIrregular) != 0
}
