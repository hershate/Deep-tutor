package boot

import (
	"log/slog"
	"strings"
	"sync"

	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/platform/repair"
)

// A successful assembly proves this user config usable. The retired Wails
// shell proved it with "the window became visible", a signal only it had;
// assembly success holds for every frontend, so the record lives here. The
// record belongs to a home, not to the process: a process serving two users
// keeps one snapshot each.
var (
	healthyConfigMu   sync.Mutex
	healthyConfigOnce = map[string]*sync.Once{}
	// recordHealthyConfig is the seam the package test replaces.
	recordHealthyConfig = repair.RecordHealthyConfigFor
)

// noteHealthyConfig records the last-known-good config snapshot once per home,
// off the boot path. A non-empty version is the host declaring a real
// installation; embedded and test assemblies leave it empty and write nothing.
// Best effort: a short headless run may exit first, and the writer is
// idempotent, so the next session records the same bytes.
func noteHealthyConfig(roots config.Roots, version string) {
	if strings.TrimSpace(version) == "" {
		return
	}
	once := healthyConfigOnceFor(roots)
	once.Do(func() {
		go func() {
			if err := recordHealthyConfig(roots, version); err != nil {
				slog.Debug("boot: record last-known-good config", "err", err)
			}
		}()
	})
}

// healthyConfigOnceFor is the once for this binding's snapshot location. Two
// homes with the same location share one; two locations never do.
func healthyConfigOnceFor(roots config.Roots) *sync.Once {
	key := roots.LastKnownGoodConfigPath()
	healthyConfigMu.Lock()
	defer healthyConfigMu.Unlock()
	once := healthyConfigOnce[key]
	if once == nil {
		once = &sync.Once{}
		healthyConfigOnce[key] = once
	}
	return once
}
