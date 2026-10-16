package i18n

import (
	"fmt"
	"strings"
	"time"
)

// Elapsed renders a run time the way the status line and the end-of-turn line
// both say it: whole seconds under a minute, then minutes and seconds, then
// hours, with a zero unit left out. Unit letters stay Latin in every locale.
func Elapsed(d time.Duration) string {
	secs := int64((d + 500*time.Millisecond) / time.Second)
	if secs < 60 {
		return fmt.Sprintf("%ds", secs)
	}
	var parts []string
	if h := secs / 3600; h > 0 {
		parts = append(parts, fmt.Sprintf("%dh", h))
	}
	if m := secs % 3600 / 60; m > 0 {
		parts = append(parts, fmt.Sprintf("%dm", m))
	}
	if s := secs % 60; s > 0 {
		parts = append(parts, fmt.Sprintf("%ds", s))
	}
	return strings.Join(parts, " ")
}
