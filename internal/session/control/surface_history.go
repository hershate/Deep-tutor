package control

import (
	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/contract/surfacespec"
)

func (c *Controller) NativeSurfaces() bool {
	if c == nil {
		return false
	}
	registry := c.mcp.registry()
	if registry == nil {
		return false
	}
	_, enabled := registry.Get(surfacespec.ToolName)
	return enabled
}

func (c *Controller) ExtensionSurfaces() []event.ExtensionSurfacePayload {
	if h := c.extensionUIHub(); h != nil {
		return h.Surfaces()
	}
	return nil
}
