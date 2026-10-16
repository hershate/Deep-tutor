package eventwire

import "reasonix/internal/contract/event"

// KindNames returns every stable frontend event kind in event.Kind order. It is
// the protocol-neutral source used by consumers such as the Remote schema
// generator; callers receive a copy and may sort it without mutating eventwire.
func KindNames() []string {
	names := make([]string, 0, int(event.KindCount))
	for kind := range event.KindCount {
		if name, ok := kindNames[kind]; ok {
			names = append(names, name)
		}
	}
	return names
}

// KindName returns the stable wire name of one event kind, or false for a
// kind outside the known set.
func KindName(kind event.Kind) (string, bool) {
	name, ok := kindNames[kind]
	return name, ok
}

var kindNames = map[event.Kind]string{
	event.TurnStarted:             "turn_started",
	event.Reasoning:               "reasoning",
	event.Text:                    "text",
	event.Message:                 "message",
	event.ToolDispatch:            "tool_dispatch",
	event.ToolResult:              "tool_result",
	event.Usage:                   "usage",
	event.Notice:                  "notice",
	event.Phase:                   "phase",
	event.ApprovalRequest:         "approval_request",
	event.AskRequest:              "ask_request",
	event.TurnDone:                "turn_done",
	event.CompactionStarted:       "compaction_started",
	event.CompactionProgress:      "compaction_progress",
	event.CompactionDone:          "compaction_done",
	event.ToolProgress:            "tool_progress",
	event.MCPSurfaceReady:         "mcp_surface_ready",
	event.Retrying:                "retrying",
	event.Steer:                   "steer",
	event.GuardianAssessment:      "guardian_assessment",
	event.ExtensionSurface:        "extension_surface",
	event.ExtensionStatus:         "extension_status",
	event.StreamAttempt:           "stream_attempt",
	event.ContextMaintenanceEvent: "context_maintenance",
	event.TodoProgressEvent:       "todo_progress",
	event.WorkspaceLeaseEvent:     "workspace_lease",
	event.WorkspaceChanged:        "workspace_changed",
	event.TurnPhase:               "turn_phase",
	event.CompletionSummary:       "completion_summary",
	event.AdjudicationsChanged:    "adjudications_changed",
	event.BrowserTabsChanged:      "browser_tabs_changed",
	event.InboxChanged:            "inbox_changed",
	event.RewindChanged:           "rewind_changed",
	event.GraphDelta:              "graph_delta",
	event.ProgressWatchEvent:      "progress_watch",
}
