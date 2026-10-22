package capdiag

import (
	"testing"

	"deep-tutor/internal/base/testenv"
)

// A machine that names its own home outranks a test's stated one, because the
// current spelling wins the lookup. The guard clears both spellings before any
// test runs, so what a test states is then what the code reads.
func TestMain(m *testing.M) {
	testenv.RunWithIsolatedUserState(m)
}
