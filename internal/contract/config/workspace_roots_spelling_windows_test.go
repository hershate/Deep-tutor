//go:build windows

package config

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

func TestVetExtraRootRefusesEveryWindowsSpellingOfASecretDir(t *testing.T) {
	cfg, _, userHome, primary, _ := rootsFixture(t)
	ssh := filepath.Join(userHome, ".ssh")
	vol := filepath.VolumeName(ssh)
	rest := strings.TrimPrefix(ssh, vol)
	for name, dir := range map[string]string{
		"extended-length": `\\?\` + ssh,
		"device":          `\\.\` + ssh,
		"loopback share":  `\\localhost\` + strings.TrimSuffix(vol, ":") + `$` + rest,
		"nt object path":  `\??\` + ssh,
		"trailing dot":    ssh + ".",
		"trailing space":  ssh + " ",
		"dot segment":     filepath.Join(ssh, "x", ".."),
		"nt unc":          `\??\UNC\localhost\` + strings.TrimSuffix(vol, ":") + `$` + rest,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := cfg.VetExtraRoot(primary, dir, nil); err == nil {
				t.Fatalf("%s was accepted", dir)
			}
			if _, refused := cfg.Roots().FolderGrantRefusal(dir); !refused {
				t.Fatalf("trust and allow_write accepted %s", dir)
			}
		})
	}
}

func TestVetExtraRootRefusesTheShortNameOfASecretDir(t *testing.T) {
	cfg, _, userHome, primary, _ := rootsFixture(t)
	ssh := filepath.Join(userHome, ".ssh")
	if err := os.MkdirAll(ssh, 0o700); err != nil {
		t.Fatal(err)
	}
	long, err := syscall.UTF16PtrFromString(ssh)
	if err != nil {
		t.Fatal(err)
	}
	buf := make([]uint16, syscall.MAX_PATH)
	n, err := syscall.GetShortPathName(long, &buf[0], uint32(len(buf)))
	if err != nil || n == 0 {
		t.Fatalf("GetShortPathName: %v", err)
	}
	short := syscall.UTF16ToString(buf[:n])
	t.Logf("short spelling: %s", short)
	if _, err := cfg.VetExtraRoot(primary, short, nil); err == nil {
		t.Fatalf("%s was accepted", short)
	}
	if _, refused := cfg.Roots().FolderGrantRefusal(short); !refused {
		t.Fatalf("trust and allow_write accepted %s", short)
	}
}
