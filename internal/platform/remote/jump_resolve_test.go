package remote

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
)

// jumpSSHConfig writes one bastion alias into a fresh home's ~/.ssh/config.
func jumpSSHConfig(t *testing.T) (string, *SSHConfigSource) {
	t.Helper()
	home := testenv.TempDir(t)
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	sshDir := filepath.Join(home, ".ssh")
	if err := os.MkdirAll(sshDir, 0o700); err != nil {
		t.Fatal(err)
	}
	text := "Host bastion\n  HostName 10.0.0.8\n  User jump-user\n  Port 2202\n  IdentityFile ~/.ssh/jump_key\n"
	if err := os.WriteFile(filepath.Join(sshDir, "config"), []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	sshCfg, err := LoadSSHConfig(filepath.Join(sshDir, "config"))
	if err != nil {
		t.Fatal(err)
	}
	return home, sshCfg
}

func assertBastionHop(t *testing.T, home string, got ResolvedHost) {
	t.Helper()
	if got.HostName != "10.0.0.8" || got.User != "jump-user" || got.Port != 2202 || got.IdentityFile != filepath.Join(home, ".ssh", "jump_key") {
		t.Fatalf("ssh_config jump was not fully resolved: %+v", got)
	}
}

// ssh -G's answer is supplied here, so this binds what ResolveJumpHosts does
// with an ssh_config alias and a configured host whether or not this machine's
// ssh gets scheduled in time.
func TestResolveJumpHostsUsesConfiguredAliasesAndSSHConfig(t *testing.T) {
	home, sshCfg := jumpSSHConfig(t)
	sshCfg.resolveOpenSSH = func(_ context.Context, path, alias string) ([]byte, error) {
		if path != sshCfg.Path() || alias != "bastion" {
			t.Fatalf("ssh -G request = path %q alias %q", path, alias)
		}
		return []byte("hostname 10.0.0.8\nuser jump-user\nport 2202\nidentityfile ~/.ssh/jump_key\n"), nil
	}
	cfg := config.Default()
	cfg.Remote.Hosts = []config.RemoteHostEntry{{
		Name: "second", Host: "10.0.0.9", User: "ops",
		PasswordEnv: "SECOND_PASSWORD", ProxyJump: "ignored-nested-hop",
	}}
	hops, err := ResolveJumpHosts(cfg, []string{"bastion", "second"}, sshCfg)
	if err != nil {
		t.Fatal(err)
	}
	assertBastionHop(t, home, hops[0])
	if got := hops[1]; got.PasswordEnv != "SECOND_PASSWORD" || len(got.ProxyJump) != 0 {
		t.Fatalf("Reasonix jump credentials/nested-chain handling wrong: %+v", got)
	}
}

// The same alias through this machine's installed ssh. A failure at the
// product's 15 s subprocess timeout is ssh not being scheduled, not a wrong
// resolution: do not shorten that timeout to make this pass.
func TestResolveJumpHostsThroughTheInstalledOpenSSH(t *testing.T) {
	if _, err := exec.LookPath("ssh"); err != nil {
		t.Skip("OpenSSH client is not installed")
	}
	home, sshCfg := jumpSSHConfig(t)
	hops, err := ResolveJumpHosts(config.Default(), []string{"bastion"}, sshCfg)
	if err != nil {
		t.Fatal(err)
	}
	assertBastionHop(t, home, hops[0])
}
