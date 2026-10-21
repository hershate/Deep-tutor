package cli

import (
	"errors"

	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/mcpdiag"
	"deep-tutor/internal/ext/plugin"
)

func reconcileRemovedMCPOAuth(workspace, name string) error {
	cfg, err := config.LoadForRoot(workspace)
	if err != nil {
		return err
	}
	remainingResource := ""
	found := false
	for _, entry := range cfg.Plugins {
		if entry.Name != name {
			continue
		}
		found = true
		remainingResource = mcpdiag.HTTPMCPOAuthResource(entry.Type, entry.URL, mcpdiag.HasAuthConfig(entry.Headers, entry.Env, entry.URL))
		break
	}
	spec := plugin.Spec{Name: name, StateDir: plugin.MCPStateDir(config.ReasonixHomeDir(), workspace, name)}
	_, err = plugin.ReconcileHTTPMCPOAuthAfterRemoval(spec, remainingResource)
	if !found {
		err = errors.Join(err, plugin.ForgetToolPins(spec.StateDir, name))
	}
	return err
}
