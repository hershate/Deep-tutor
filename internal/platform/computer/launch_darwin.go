package computer

import "strings"

func nativeLaunchSpecification(app nativeApplication) (launchSpecification, error) {
	spec := launchSpecification{identity: app.Bundle}
	if app.Bundle == "" || strings.ContainsAny(app.Bundle, "/\\: \t\r\n") {
		return spec, fail(CodeUnsupported, "launch requires a native application bundle identity")
	}
	spec.args = map[string]any{"bundle_id": app.Bundle}
	return spec, nil
}

func sameExecutablePath(actual, expected string) bool { return actual == expected }

func attestNativeLaunchBinary(_ string) error {
	return fail(CodeUnsupported, "macOS launch uses a registered native bundle")
}
