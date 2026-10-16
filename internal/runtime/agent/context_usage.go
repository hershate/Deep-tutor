package agent

import "reasonix/internal/contract/tool"

// contextUsage memoises the projected prompt size. The estimate walks every
// visible message, and status gauges redraw far more often than the view moves,
// so it is keyed on everything that can change the answer: the transcript,
// projection, calibration, and provider-visible tool schemas.
type contextUsage struct {
	transcriptVersion  uint64
	projectionVersion  uint64
	calibration        *promptTokenCalibration
	anchor             *usageAnchor
	tools              *tool.Registry
	toolSchemaRevision uint64
	tokens             int
}

// ContextUsedTokens is the prompt size the next request sends, measured the way
// the fold trigger measures it: the local estimate, raised to the provider's last
// reported size plus what the view gained since (providerReportedFloor). The hard
// ceiling reads the estimate alone. After a fold or on a rebound session there is
// no anchor, and the estimate stands.
func (a *Agent) ContextUsedTokens() int {
	if a == nil {
		return 0
	}
	return a.window().contextUsedTokens()
}

func (a *contextWindow) contextUsedTokens() int {
	session := a.Session()
	if session == nil {
		return 0
	}
	transcriptVersion := session.TranscriptVersion()
	projectionVersion := a.currentProjectionVersion()
	calibration := a.sess.output.promptCalibration.Load()
	anchor := a.sess.output.usageAnchor.Load()
	tools := a.svc.tools
	toolSchemaRevision := tools.SchemaRevision()
	if cached := a.sess.output.contextUsage.Load(); cached != nil &&
		cached.transcriptVersion == transcriptVersion &&
		cached.projectionVersion == projectionVersion &&
		cached.calibration == calibration &&
		cached.anchor == anchor &&
		cached.tools == tools &&
		cached.toolSchemaRevision == toolSchemaRevision {
		return cached.tokens
	}
	visible := a.modelVisibleMessages()
	tokens := a.estimatedVisibleRequestTokens(visible)
	if floor, ok := a.providerReportedFloor(a.visibleRequestShape(visible), projectionVersion); ok {
		tokens = max(tokens, floor)
	}
	a.sess.output.contextUsage.Store(&contextUsage{
		transcriptVersion:  transcriptVersion,
		projectionVersion:  projectionVersion,
		calibration:        calibration,
		anchor:             anchor,
		tools:              tools,
		toolSchemaRevision: toolSchemaRevision,
		tokens:             tokens,
	})
	return tokens
}
