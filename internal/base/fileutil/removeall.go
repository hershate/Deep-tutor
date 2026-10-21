package fileutil

import (
	"os"
	"time"
)

// RemoveAll deletes a directory tree, retrying the transient locks Remove
// retries. Go's os.RemoveAll does not retry: one file inside the tree that
// cannot be unlinked stops it and leaves a half-deleted tree behind, and a
// path that is already gone is a success.
func RemoveAll(path string) error {
	var err error
	for attempt := 0; ; attempt++ {
		if err = removeAllTree(path); err == nil {
			return nil
		}
		if attempt >= maxReplaceRetries {
			return err
		}
		time.Sleep(time.Duration(attempt+1) * replaceRetryBase)
	}
}

// removeAllTree is the test seam: the transient lock a scanner holds cannot
// be provoked portably on a real filesystem.
var removeAllTree = os.RemoveAll
