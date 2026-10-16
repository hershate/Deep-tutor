package delegation

import (
	"reasonix/internal/contract/tool"
)

func legacyTaskTargetAccess(checks []tool.TargetAccessCheck) tool.TargetAccessCheck {
	if len(checks) == 1 && checks[0] != nil {
		return checks[0]
	}
	return tool.UnconfiguredFileAccess
}
