package i18n

import (
	"testing"
	"time"
)

func TestElapsed(t *testing.T) {
	for _, c := range []struct {
		d    time.Duration
		want string
	}{
		{0, "0s"}, {499 * time.Millisecond, "0s"}, {500 * time.Millisecond, "1s"}, {59 * time.Second, "59s"},
		{59600 * time.Millisecond, "1m"}, {60 * time.Second, "1m"}, {65 * time.Second, "1m 5s"},
		{3600 * time.Second, "1h"}, {3725 * time.Second, "1h 2m 5s"}, {3605 * time.Second, "1h 5s"},
	} {
		if got := Elapsed(c.d); got != c.want {
			t.Errorf("Elapsed(%v) = %q, want %q", c.d, got, c.want)
		}
	}
}
