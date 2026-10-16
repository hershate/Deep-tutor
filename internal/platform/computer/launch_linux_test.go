package computer

import (
	"os"
	"testing"
)

func TestCUALinuxLaunchRejectsArgumentsAndCommandExpansion(t *testing.T) {
	for _, command := range []string{"/usr/bin/app --flag", "/usr/bin/app; /bin/sh", "/usr/bin/app$(touch /tmp/marker)", "'/tmp/app;touch${IFS}/tmp/marker'", "FOO=x /usr/bin/app", "/usr/bin/app > /tmp/out", "app", ""} {
		if _, err := nativeLaunchSpecification(nativeApplication{Bundle: "example.desktop", LaunchPath: command}); CodeOf(err) != CodeUnsupported {
			t.Fatalf("launch command accepted %q: %v", command, err)
		}
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	spec, err := applicationLaunch(nativeApplication{Bundle: "example.desktop", LaunchPath: exe})
	if err != nil || spec.args["launch_path"] != spec.executable {
		t.Fatalf("native ELF launch: %+v %v", spec, err)
	}
}

func TestCUALinuxInstalledApplicationIsListedAsLaunchableOnlyWhenItCanBe(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("REASONIX_CUA_INSTALLED_APP", exe)
	t.Setenv("REASONIX_CUA_INSTALLED_ID", "example.desktop")
	t.Setenv("REASONIX_CUA_LAUNCH_PATH", exe)
	apps, err := fakeSession(t).Apps(t.Context())
	if err != nil || len(apps) != 1 || !apps[0].Launchable || apps[0].Bundle != "example.desktop" || apps[0].PID != 0 {
		t.Fatalf("a launchable installed application: %+v %v", apps, err)
	}
	t.Setenv("REASONIX_CUA_LAUNCH_PATH", "app --flag")
	apps, err = fakeSession(t).Apps(t.Context())
	if err != nil || len(apps) != 1 || apps[0].Launchable {
		t.Fatalf("an installed application that cannot be launched was offered: %+v %v", apps, err)
	}
}
