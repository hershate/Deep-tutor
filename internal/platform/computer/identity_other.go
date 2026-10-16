//go:build !windows && !linux && !darwin

package computer

func processIdentity(_ int32, _ string) (string, string, error) {
	return "", "", fail(CodeUnsupported, "CUA platform is unsupported")
}
