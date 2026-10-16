package computer

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"image"
	"strings"

	"reasonix/internal/model/visionimage"
)

type Snapshot struct {
	App       App
	Modals    []Modal
	Lines     []string
	Truncated bool
	Note      string
}

type driverSnapshot struct {
	PID            int32  `json:"pid"`
	WindowID       int    `json:"window_id"`
	CaptureID      string `json:"capture_id"`
	SnapshotID     string `json:"snapshot_id"`
	Bounds         Rect   `json:"window_bounds"`
	Truncated      bool   `json:"truncated"`
	DegradedReason string `json:"degraded_reason"`
	Elements       []struct {
		Token string `json:"element_token"`
		Role  string `json:"role"`
		Label string `json:"label"`
		Value string `json:"value"`
		Depth int    `json:"depth"`
	} `json:"elements"`
}

func (s *Session) observe(ctx context.Context, bundle string, ids []int, capture bool) (Snapshot, string, error) {
	s.state = observation{}
	app, err := s.app(ctx, bundle)
	if err != nil {
		return Snapshot{}, "", err
	}
	window, err := exactWindow(app, ids)
	if err != nil {
		return Snapshot{}, "", err
	}
	var data driverSnapshot
	result, err := s.read(ctx, "get_window_state", map[string]any{"pid": app.PID, "window_id": window.ID, "max_elements": 500, "max_depth": 25, "max_image_dimension": 0, "include_screenshot": capture, "include_accessibility_tree": !capture}, &data)
	if err != nil {
		return Snapshot{}, "", err
	}
	if data.PID != app.PID || data.WindowID != window.ID {
		s.state = observation{}
		return Snapshot{}, "", fail(CodeFailed, "CUA snapshot target mismatch")
	}
	state := observation{generation: s.driver.Generation(), app: app, window: window, refs: map[string]string{}, capture: data.CaptureID}
	snap := Snapshot{App: app, Truncated: data.Truncated, Note: boundedDriverNote(data.DegradedReason)}
	for _, el := range data.Elements {
		if el.Depth < 0 || el.Depth > 25 {
			return Snapshot{}, "", fail(CodeFailed, "invalid CUA element depth")
		}
		line := strings.Repeat("  ", el.Depth) + "- " + ShownBundle(el.Role) + " " + ShownName(el.Label)
		if el.Token != "" {
			if !validToken(el.Token) {
				return Snapshot{}, "", fail(CodeFailed, "invalid CUA element token")
			}
			ref := "r" + rand.Text()
			state.refs[ref] = el.Token
			line += " [" + ref + "]"
		}
		if el.Value != "" {
			line += " value=" + ShownName(el.Value)
		}
		snap.Lines = append(snap.Lines, line)
	}
	var shot string
	for _, content := range result.Content {
		if !capture || content.Type != "image" {
			continue
		}
		raw, err := base64.StdEncoding.DecodeString(content.Data)
		if err != nil {
			return Snapshot{}, "", fail(CodeCaptureFailed, "invalid CUA image encoding")
		}
		original, _, err := image.DecodeConfig(bytes.NewReader(raw))
		if err != nil {
			return Snapshot{}, "", fail(CodeCaptureFailed, "invalid CUA image")
		}
		fitted, mime, err := visionimage.Fit(raw, content.MIME)
		if err != nil {
			return Snapshot{}, "", fail(CodeCaptureFailed, "%v", err)
		}
		size, _, err := image.DecodeConfig(bytes.NewReader(fitted))
		if err != nil || size.Width <= 0 || size.Height <= 0 {
			return Snapshot{}, "", fail(CodeCaptureFailed, "invalid fitted image")
		}
		state.imageWidth = size.Width
		state.imageHeight = size.Height
		state.captureWidth = original.Width
		state.captureHeight = original.Height
		shot = "data:" + mime + ";base64," + base64.StdEncoding.EncodeToString(fitted)
		break
	}
	s.state = state
	return snap, shot, nil
}

func (s *Session) Snapshot(ctx context.Context, bundle string, windows ...int) (Snapshot, error) {
	s.driver.desktop.Lock()
	defer s.driver.desktop.Unlock()
	snap, _, err := s.observe(ctx, bundle, windows, false)
	return snap, err
}

func (s *Session) Screenshot(ctx context.Context, bundle string, windows ...int) (string, App, error) {
	s.driver.desktop.Lock()
	defer s.driver.desktop.Unlock()
	snap, shot, err := s.observe(ctx, bundle, windows, true)
	if err == nil && shot == "" {
		err = fail(CodeCaptureFailed, "CUA returned no screenshot")
	}
	return shot, snap.App, err
}

func boundedDriverNote(note string) string {
	if note == "" {
		return ""
	}
	return ShownName(note)
}

func validToken(token string) bool {
	if len(token) == 0 || len(token) > 128 {
		return false
	}
	for _, char := range token {
		if char >= 'a' && char <= 'z' || char >= 'A' && char <= 'Z' || char >= '0' && char <= '9' || char == ':' || char == '-' || char == '_' {
			continue
		}
		return false
	}
	return true
}
