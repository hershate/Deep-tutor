package computer

func processIdentity(_ int32, bundle string) (string, string, error) {
	if bundle == "" {
		return "", "", fail(CodeNoApp, "CUA omitted application identity")
	}
	return bundle, "", nil
}

func processExecutable(_ int32) (string, error) {
	return "", fail(CodeUnsupported, "macOS launch identity uses the native bundle contract")
}
