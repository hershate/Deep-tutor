package boot

import "deep-tutor/internal/safety/sandbox"

func pinnedEffectShell() *sandbox.Shell {
	return &sandbox.Shell{Kind: sandbox.ShellBash, Path: "bash"}
}
