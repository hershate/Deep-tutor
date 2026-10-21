//go:build linux

package repair

import "deep-tutor/internal/base/fileutil"

func renameRepairNodeNoReplace(oldPath, newPath string) error {
	return fileutil.RenameNoReplace(oldPath, newPath)
}
