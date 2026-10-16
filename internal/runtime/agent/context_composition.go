package agent

import (
	"cmp"
	"fmt"
	"slices"
	"strings"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
)

const largestContextItems = 3

// contextComposition measures what holds the visible prompt's tokens, by the
// structure of the transcript: role, whether a message is a fold digest, and
// whether it belongs to the turn still running. Nothing here reads wording.
func (a *contextWindow) contextComposition() event.ContextComposition {
	visible := a.modelVisibleMessages()
	var system, digest, user, reply, output []provider.Message
	for _, m := range visible {
		switch {
		case m.Role == provider.RoleSystem:
			system = append(system, m)
		case isCompactionSummary(m):
			digest = append(digest, m)
		case m.Role == provider.RoleUser:
			user = append(user, m)
		case m.Role == provider.RoleAssistant:
			reply = append(reply, m)
		case m.Role == provider.RoleTool:
			output = append(output, m)
		}
	}
	parts := []event.ContextPart{
		{Name: "system", Tokens: a.classTokens(system, false)},
		{Name: "tools", Tokens: a.classTokens(nil, true)},
		{Name: "digest", Tokens: a.classTokens(digest, false)},
		{Name: "user", Tokens: a.classTokens(user, false)},
		{Name: "reply", Tokens: a.classTokens(reply, false)},
		{Name: "tool_output", Tokens: a.classTokens(output, false)},
	}
	slices.SortStableFunc(parts, func(x, y event.ContextPart) int { return cmp.Compare(y.Tokens, x.Tokens) })
	out := event.ContextComposition{Total: a.estimatedVisibleRequestTokens(visible), At: a.compactTrigger(), Parts: parts}

	active := a.activeTurnStart(visible)
	if active >= 0 {
		out.Active = a.classTokens(visible[active:], false)
	}
	items := make([]event.ContextItem, 0, len(visible))
	for i, m := range visible {
		if m.Role == provider.RoleSystem || isCompactionSummary(m) {
			continue
		}
		item := event.ContextItem{Role: string(m.Role), Tokens: fixedTokenEstimate(m), Active: active >= 0 && i >= active}
		if m.Role == provider.RoleTool {
			if j := findToolCaller(visible, i, m.ToolCallID); j >= 0 {
				item.Tool = toolCallName(visible[j], m.ToolCallID)
			}
		}
		items = append(items, item)
	}
	slices.SortStableFunc(items, func(x, y event.ContextItem) int { return cmp.Compare(y.Tokens, x.Tokens) })
	out.Largest = items[:min(largestContextItems, len(items))]
	return out
}

func toolCallName(caller provider.Message, id string) string {
	for _, tc := range caller.ToolCalls {
		if tc.ID == id {
			return tc.Name
		}
	}
	return ""
}

// noteContextComposition tells the user what an over-threshold prompt is made
// of. The sentence is the English fallback; a frontend words the typed payload.
func (a *contextWindow) noteContextComposition() {
	if a == nil || a.svc.sink == nil {
		return
	}
	c := a.contextComposition()
	a.svc.sink.Emit(event.Event{
		Kind: event.Notice, Level: event.LevelWarn, Code: event.NoticeCodeContextComposition,
		Detail: c.Encode(), Text: compositionSentence(c),
	})
}

func compositionSentence(c event.ContextComposition) string {
	var top []string
	for _, p := range c.Parts[:min(3, len(c.Parts))] {
		top = append(top, fmt.Sprintf("%s %d", strings.ReplaceAll(p.Name, "_", " "), p.Tokens))
	}
	s := fmt.Sprintf("The context holds about %d tokens, over the %d compaction threshold; largest parts: %s.", c.Total, c.At, strings.Join(top, ", "))
	if c.Active > 0 {
		s += fmt.Sprintf(" The turn in progress holds %d of them, and a fold cannot take it.", c.Active)
	}
	return s
}
