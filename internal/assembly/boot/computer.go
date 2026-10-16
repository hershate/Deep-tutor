package boot

import (
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/tool"
	"reasonix/internal/platform/browser"
	"reasonix/internal/platform/computer"
	"reasonix/internal/tools/builtin"
)

// bindMachineTools binds the tools that reach past the workspace into this
// machine: its browser, and its applications when the host provides a driver. They
// replace the unbound built-ins, so they are bound after those are registered.
func bindMachineTools(reg *tool.Registry, cfg *config.Config, root string, reused *browser.Session, driver *computer.Driver) *browser.Session {
	if driver != nil {
		for _, t := range builtin.ComputerTools(computer.NewSession(driver)) {
			reg.Add(t)
		}
	}
	return bindBrowser(reg, cfg, root, reused)
}
