package tool

import (
	"context"
	"encoding/json"
)

const CodeTargetAccessUnconfigured = "workspace.target_access_unconfigured"

// UnconfiguredFileAccess refuses resolvable file calls without an access policy.
func UnconfiguredFileAccess(_ context.Context, target Tool, _ json.RawMessage) error {
	_, reader := target.(ReadTargeter)
	_, sourceReader := target.(ReadPathResolver)
	if !WritesNamedPaths(target) && !reader && !sourceReader {
		return nil
	}
	return Refusal{Code: CodeTargetAccessUnconfigured, Message: "File access policy is not configured; this file operation cannot run."}
}
