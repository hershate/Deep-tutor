package computer

import (
	"context"
	"math"
	"strings"
	"time"
)

func (s *Session) point(app App, window Window, x, y *float64) (float64, float64, error) {
	state := s.state
	if state.generation != s.driver.Generation() || state.app.PID != app.PID || state.app.Bundle != app.Bundle || state.window.ID != window.ID || state.capture == "" || state.imageWidth <= 0 {
		return 0, 0, fail(CodeNeedsScreenshot, "take a fresh screenshot of this window")
	}
	if x == nil || y == nil || math.IsNaN(*x) || math.IsNaN(*y) || math.IsInf(*x, 0) || math.IsInf(*y, 0) || *x < 0 || *y < 0 || *x >= float64(state.imageWidth) || *y >= float64(state.imageHeight) {
		return 0, 0, fail(CodeBadStep, "point is outside the screenshot")
	}
	return *x * float64(state.captureWidth) / float64(state.imageWidth), *y * float64(state.captureHeight) / float64(state.imageHeight), nil
}

func (s *Session) step(ctx context.Context, app App, window Window, step Step) (StepResult, error) {
	if step.Action == "wait" {
		return waitStep(ctx, step.Ms)
	}
	args := map[string]any{"pid": app.PID, "window_id": window.ID, "delivery_mode": "background"}
	if step.Ref != "" {
		if s.state.generation != s.driver.Generation() || s.state.app.PID != app.PID || s.state.window.ID != window.ID || s.state.refs[step.Ref] == "" {
			return StepResult{}, fail(CodeStaleRef, "take a fresh snapshot; ref is not in this window")
		}
		args["element_token"] = s.state.refs[step.Ref]
	}
	if err := validateStep(step); err != nil {
		return StepResult{}, err
	}
	name := step.Action
	switch step.Action {
	case "click", "pointer_click":
		name = "click"
		if step.Ref == "" {
			x, y, err := s.point(app, window, step.X, step.Y)
			if err != nil {
				return StepResult{}, err
			}
			args["x"] = x
			args["y"] = y
			args["capture_id"] = s.state.capture
			s.state.capture = ""
		}
		if step.Action == "pointer_click" {
			args["delivery_mode"] = "foreground"
		}
		if step.Button != "" {
			args["button"] = step.Button
		}
		if step.Times > 0 {
			args["count"] = step.Times
		}
	case "right_click":
		if step.Ref == "" {
			return StepResult{}, fail(CodeBadStep, "right_click requires a ref")
		}
	case "set_value":
		if step.Ref == "" {
			return StepResult{}, fail(CodeBadStep, "set_value requires a ref")
		}
		args["value"] = step.Text
	case "type", "paste":
		name = "type_text"
		args["text"] = step.Text
	case "key":
		name = "press_key"
		args["key"] = step.Key
		if strings.Contains(step.Key, "+") {
			name = "hotkey"
			delete(args, "key")
			args["keys"] = strings.Split(step.Key, "+")
		}
		if step.Times > 1 {
			return StepResult{}, fail(CodeUnsupported, "repeat key as separate steps")
		}
	case "scroll":
		if math.IsNaN(step.Amount) || math.IsInf(step.Amount, 0) || math.Abs(step.Amount) > 100 {
			return StepResult{}, fail(CodeBadStep, "scroll amount must be bounded")
		}
		direction := "up"
		if step.Amount < 0 {
			direction = "down"
		}
		args["amount"] = int(math.Abs(step.Amount))
		args["direction"] = direction
		args["by"] = "line"
	case "focus", "hold_key", "pointer_move", "pointer_drag", "pointer_position":
		return StepResult{}, fail(CodeUnsupported, "%s has no equivalent safe CUA operation", step.Action)
	default:
		return StepResult{}, fail(CodeBadStep, "unknown action %q", step.Action)
	}
	var action DriverAction
	if _, err := s.read(ctx, name, args, &action); err != nil {
		return StepResult{}, err
	}
	return finishAction(action.nativeContract(name))
}

func finishAction(action DriverAction) (StepResult, error) {
	switch action.Effect {
	case "confirmed", "partial", "unverifiable", "suspected_noop":
	default:
		return StepResult{}, fail(CodeFailed, "invalid CUA action effect")
	}
	switch action.Route {
	case "accessibility", "synthetic_events", "global_input", "system_api", "dom", "trusted_input":
	default:
		return StepResult{}, fail(CodeFailed, "invalid CUA action route")
	}
	if action.Delivery != nil {
		switch action.Delivery.Mode {
		case "background", "foreground", "not_applicable", "unknown":
		default:
			return StepResult{}, fail(CodeFailed, "invalid CUA delivery mode")
		}
		if action.Delivery.Count != nil && *action.Delivery.Count < 0 {
			return StepResult{}, fail(CodeFailed, "invalid CUA delivery count")
		}
	}
	done := StepResult{Note: ShownName(action.Summary), Effect: action.project()}
	if action.Effect == "partial" || action.Effect == "suspected_noop" {
		return done, fail(CodeEffectUnknown, "CUA effect=%s, route=%s; inspect fresh state before retry", action.Effect, action.Route)
	}
	return done, nil
}

func waitStep(ctx context.Context, ms int) (StepResult, error) {
	delay := time.Duration(ms) * time.Millisecond
	if delay < 0 || delay > maxWait {
		return StepResult{}, fail(CodeBadStep, "wait must be between zero and 30 seconds")
	}
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return StepResult{}, ctx.Err()
	case <-timer.C:
		return StepResult{Note: "wait"}, nil
	}
}

func validateStep(step Step) error {
	if step.Ref != "" && (step.X != nil || step.Y != nil) {
		return fail(CodeBadStep, "choose element ref or screenshot coordinates")
	}
	if step.Times < 0 || step.Times > 3 {
		return fail(CodeBadStep, "click count must be between zero and three")
	}
	return nil
}
