//go:build windows

package config

import (
	"os/exec"
	"path/filepath"
	"testing"
)

func TestVetExtraRootSeesThroughAJunctionToTheSSHDir(t *testing.T) {
	cfg, _, userHome, primary, extraFolder := rootsFixture(t)
	link := filepath.Join(extraFolder(), "innocent")
	if out, err := exec.Command("cmd", "/c", "mklink", "/J", link, filepath.Join(userHome, ".ssh")).CombinedOutput(); err != nil {
		t.Skipf("junctions unavailable: %v %s", err, out)
	}
	if got := refusalOf(t, cfg, primary, link); got != RootProtected {
		t.Fatalf("a junction to ~/.ssh was offered: %s", got)
	}
}

func TestVetExtraRootRefusesAShareRoot(t *testing.T) {
	cfg, _, _, primary, _ := rootsFixture(t)
	if got := refusalOf(t, cfg, primary, `\\server\share\folder`); got != RootNotDirectory {
		t.Fatalf("a network share path: %s", got)
	}
}
