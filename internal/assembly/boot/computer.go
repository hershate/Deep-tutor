package boot

import (
	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/tool"
	"deep-tutor/internal/platform/browser"
	"deep-tutor/internal/platform/computer"
	"deep-tutor/internal/tools/builtin"
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
