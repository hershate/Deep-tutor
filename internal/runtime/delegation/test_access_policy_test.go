package delegation

import (
	"context"
	"encoding/json"
	"reasonix/internal/contract/tool"
)

func allowTestFileAccess(context.Context, tool.Tool, json.RawMessage) error { return nil }
