package testenv

import (
	"os"
	"time"
)

const (
	// removeAttempts is how many times a tree removal is tried before it is
	// reported as unconverged.
	removeAttempts = 12
	// removeBackoff is the base of the retry delay: the wait before the
	// n-th retry is n * removeBackoff.
	removeBackoff = 20 * time.Millisecond
)

// removeTree deletes a directory tree, retrying the removals that lose to a
// handle that has not closed yet.
//
// Windows keeps a deleted file's directory entry until its last handle closes,
// so a tree whose reader has not finished cannot be removed on the first try.
func removeTree(path string, attempts int) error {
	var err error
	for attempt := 0; ; attempt++ {
		if err = os.RemoveAll(path); err == nil {
			return nil
		}
		if attempt >= attempts {
			return err
		}
		time.Sleep(time.Duration(attempt+1) * removeBackoff)
	}
}
