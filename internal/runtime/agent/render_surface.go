package agent

import (
	"context"
	"encoding/json"
	"errors"

	"reasonix/internal/base/secrets"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/contract/tool"
)

type NativeSurfacePublisher interface {
	PublishNativeSurface(callID, surfaceID string) error
}

type RenderSurfaceTool struct{}

func NewRenderSurfaceTool() *RenderSurfaceTool { return &RenderSurfaceTool{} }
func (*RenderSurfaceTool) Name() string        { return surfacespec.ToolName }
func (*RenderSurfaceTool) Description() string {
	return "Build an interactive task workspace: map for geographic places and ordered itineraries, comparison for product choices, dashboard for data exploration, plus forms, choice cards, editable_text and diff_preview. Map uses a multichoice field whose options each have location {latitude,longitude} and optional group (such as a day); submitted ids preserve visit order. Comparison uses choice/multichoice options with facts {label,value}, optional group and price/currency. Map and comparison options can include image {path,alt,source} referencing an existing workspace-relative raster file. For real product/place photos, research a source page with web_fetch, then use import_web_image to save an observed candidate and copy its returned image object including source. Omit images if acquisition fails; never use generated substitutes for real entities. Dashboard uses dataset {categories,series:[{id,label,unit,values}]} with equal-length finite numeric series. Compose these with text, fields and buttons for a complete task. Use researched coordinates and data; never invent a location or metric. Map connections indicate visit order, not road routing. Fields accept string, number, bool, choice or multichoice values keyed by stable ids. Drafts use string fields; diff previews carry before/after text and do not modify files. Display-only trees return a summary. Trees with button nodes return immediately; clicks deliver typed surface_action notes through the controller. Forms without buttons wait through ask. Never use surfaces to approve tools or grant permissions. No HTML, CSS, remote image URLs or executable components."
}
func (*RenderSurfaceTool) ReadOnly() bool        { return true }
func (*RenderSurfaceTool) Reach() tool.Reach     { return tool.ReachHostControl }
func (*RenderSurfaceTool) DecisionBarrier() bool { return true }
func (*RenderSurfaceTool) SnipHint() tool.SnipHint {
	return tool.SnipHint{Head: 4, Tail: 2, HeadChars: 3000, TailChars: 1000}
}
func (*RenderSurfaceTool) Schema() json.RawMessage { return surfaceSchema }

func (*RenderSurfaceTool) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	s, err := surfacespec.Parse(args)
	if err != nil {
		return "", err
	}
	s.Redact(secrets.RedactCredentials)
	if err := s.Validate(); err != nil {
		return "", err
	}
	id := s.ID()
	if len(s.Actions()) > 0 {
		actionCallID, _, actionAsker, available := CallContext(ctx)
		if !available || actionAsker == nil {
			return "unresolved: this run has no interactive user. Do not supply their answers or choose on their behalf.", nil
		}
		publisher, canPublish := actionAsker.(NativeSurfacePublisher)
		if !canPublish {
			return "unresolved: this run has no native surface publisher. Use an ordinary question instead.", nil
		}
		if err := publisher.PublishNativeSurface(actionCallID, id); err != nil {
			return "", err
		}
		result, marshalErr := json.Marshal(map[string]any{"surface_id": id, "nonblocking": true})
		return string(result), marshalErr
	}
	if len(s.Fields()) == 0 {
		return "surface_id: " + id + "\nDisplayed: " + s.Title, nil
	}
	callID, _, asker, ok := CallContext(ctx)
	if !ok || asker == nil {
		return "unresolved: this run has no interactive user. Do not supply their answers or choose on their behalf.", nil
	}
	answers, err := asker.Ask(ctx, []event.AskQuestion{{ID: "surface", Header: s.Title, Prompt: s.Title, Surface: s, CallID: callID}})
	if timedOut, ok := errors.AsType[*AskTimedOutError](err); ok {
		return timedOut.result(), nil
	}
	if err != nil {
		return "", err
	}
	if len(answers) == 0 {
		return "The user dismissed the surface without answering. Stop and wait for their next message.", nil
	}
	if len(answers) != 1 || answers[0].QuestionID != "surface" || len(answers[0].Selected) != 0 {
		return "", surfacespec.ErrValueInvalid
	}
	values, err := s.ParseAnswers(answers[0].Value)
	if err != nil {
		return "", err
	}
	result, err := json.Marshal(struct {
		SurfaceID string                     `json:"surface_id"`
		Values    map[string]json.RawMessage `json:"values"`
	}{id, values})
	return string(result), err
}
