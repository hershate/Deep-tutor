package cli

import (
	"time"

	"deep-tutor/internal/session/control"
	"deep-tutor/internal/state/memory"
)

func renderMemory(width int, set *memory.Set) string {
	return viewProtectLines(control.RenderMemorySummary(set, time.Now().UTC()), width)
}
