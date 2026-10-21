package doctor

import (
	"testing"

	"deep-tutor/internal/base/testenv"
)

func TestMain(m *testing.M) {
	testenv.RunWithIsolatedUserState(m)
}
