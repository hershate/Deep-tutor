package fileutil

import (
	"os"
	"time"
)

// RemoveAll deletes a directory tree, retrying the transient locks Remove
// retries.
//
// Go's os.RemoveAll does not retry: the moment one file inside the tree
// cannot be unlinked — a scanner, an indexer or a reader that has not closed
// its handle yet — it stops and reports "the directory is not empty", leaving
// the caller with a half-deleted tree and an error it cannot act on. A tree
// holds more files than a single file does, so its window is the wider one.
//
// A path that is already gone is a success, not a race to re-report.
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
