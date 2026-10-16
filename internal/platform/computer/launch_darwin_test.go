package computer

import "testing"

func TestCUAMacLaunchRequiresAnExactRegisteredBundle(t *testing.T) {
	if _, err := applicationLaunch(nativeApplication{Bundle: "com.apple.Terminal"}); CodeOf(err) != CodeAppRefused {
		t.Fatal(err)
	}
	for _, identity := range []string{"", "/Applications/Notes.app", "com.example Notes"} {
		if _, err := applicationLaunch(nativeApplication{Bundle: identity}); CodeOf(err) != CodeUnsupported {
			t.Fatalf("bundle accepted %q: %v", identity, err)
		}
	}
	spec, err := applicationLaunch(nativeApplication{Bundle: "com.apple.Notes"})
	if err != nil || spec.args["bundle_id"] != "com.apple.Notes" {
		t.Fatalf("bundle launch: %+v %v", spec, err)
	}
}
