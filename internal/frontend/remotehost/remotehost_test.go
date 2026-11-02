package remotehost

import (
	"fmt"
	"testing"

	"deep-tutor/internal/platform/remote/bootstrap"
)

func TestServeOccupancyFailuresCarryTheirOwnCode(t *testing.T) {
	for _, tc := range []struct {
		err  error
		code string
	}{
		{bootstrap.ErrServeProviderMismatch, "remote.serve_provider_mismatch"},
		{bootstrap.ErrServeNotAttachable, "remote.serve_not_attachable"},
	} {
		if got := installFailureCode(fmt.Errorf("pid 7: %w", tc.err)); got != tc.code {
			t.Errorf("code for %v = %q, want %q", tc.err, got, tc.code)
		}
	}
}
