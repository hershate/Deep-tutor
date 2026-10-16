package serve

import (
	"testing"

	"reasonix/internal/assembly/boot"
	"reasonix/internal/contract/config"
	"reasonix/internal/platform/computer"
	"reasonix/internal/session/control"
)

func TestComputerDriverSurvivesRuntimeRebuildsWithoutAmbientAuthority(t *testing.T) {
	bc := NewBroadcaster()
	ctrl := control.New(control.Options{Sink: bc})
	driver := computer.NewDriver("/unused", nil)
	defer driver.Close()
	served := New(ctrl, bc, config.ServeConfig{})
	served.AdoptRuntime(&boot.BuildResult{ComputerDriver: driver})
	for name, opts := range map[string]boot.Options{
		"model":     served.rebuildOptions(ctrl, "next"),
		"reload":    served.reloadOptions(ctrl, "next"),
		"workspace": served.workspaceOptions("/other", "next"),
	} {
		if opts.ComputerDriver != driver {
			t.Errorf("%s lost the host driver", name)
		}
	}
	headless := New(ctrl, bc, config.ServeConfig{})
	if headless.rebuildOptions(ctrl, "next").ComputerDriver != nil {
		t.Fatal("unrelated server acquired computer authority")
	}
}
