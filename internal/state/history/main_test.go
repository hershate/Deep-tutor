package history

import (
	"testing"

	"deep-tutor/internal/base/testenv"
)

// A machine that names its own home outranks a test's stated one, because the
// current spelling wins the lookup. The guard clears both spellings before any
// test runs, so what a test states is then what the code reads. This package
// closes a process-wide catalog, and that catalog's SQLite lives under the
// cache root — without the guard the close wrote into the machine's own home.
func TestMain(m *testing.M) {
	testenv.RunWithIsolatedUserState(m)
}
