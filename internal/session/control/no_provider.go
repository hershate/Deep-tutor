package control

import "reasonix/internal/contract/config"

// NoProvider reports that this runtime started with no provider configured, so
// a turn can only answer provider.ErrNoProvider until one is added.
func (c *Controller) NoProvider() bool { return c.modelRef == config.UnconfiguredRef }
