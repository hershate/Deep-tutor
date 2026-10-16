package uihub

import (
	"context"
	"strings"
	"testing"

	"reasonix/internal/contract/event"
	"reasonix/internal/ext/extension/protocol"
)

func TestPublishedSurfacesReplayLatestRedactedState(t *testing.T) {
	h := newTestHub(&eventRecorder{})
	for _, text := range []string{"old", "api_key=sk-abcdef1234567890SECRETKEY"} {
		publishRaw(t, h, "alpha", protocol.UIPublishParams{SurfaceID: "view", SessionID: "sess-1", Generation: 7, Kind: protocol.UISurfaceView,
			Payload: mustRaw(t, map[string]any{"body": []map[string]any{{"kind": "text", "value": text}}})})
	}
	got := h.Surfaces()
	if len(got) != 1 || got[0].View == nil || strings.Contains(got[0].View.Body[0].Value, "SECRETKEY") {
		t.Fatalf("snapshot: %+v", got)
	}
	got[0].View.Body[0].Value = "mutated by reader"
	if h.Surfaces()[0].View.Body[0].Value == "mutated by reader" {
		t.Fatal("snapshot aliases live state")
	}
	h.BindGeneration("sess-1", 8)
	expired := h.Surfaces()
	if len(expired) != 1 || expired[0].Code != event.SurfaceExpired || expired[0].View != nil {
		t.Fatalf("old generation stays actionable: %+v", expired)
	}
	stale, err := h.HandlerFor("alpha").Publish(context.Background(), protocol.UIPublishParams{SurfaceID: "view", SessionID: "sess-1", Generation: 7, Kind: protocol.UISurfaceView, Payload: mustRaw(t, map[string]any{"body": []any{}})})
	if err != nil || stale.Accepted || h.Surfaces()[0].Code != event.SurfaceExpired {
		t.Fatalf("stale publication revived surface: %+v %v", stale, err)
	}
	h.BindGeneration("sess-2", 9)
	if len(h.Surfaces()) != 0 {
		t.Fatal("surface crossed into another session")
	}
}

func TestSurfaceReplayExcludesTransientNotifications(t *testing.T) {
	h := newTestHub(&eventRecorder{})
	publishRaw(t, h, "alpha", protocol.UIPublishParams{SurfaceID: "toast", SessionID: "sess-1", Generation: 7, Kind: protocol.UISurfaceNotification, Payload: mustRaw(t, map[string]any{"title": "once"})})
	if len(h.Surfaces()) != 0 {
		t.Fatal("notification replays")
	}
}

func TestCrashedSurfaceNeedsRepublishingBeforeItCanRevive(t *testing.T) {
	h := newTestHub(&eventRecorder{})
	params := protocol.UIPublishParams{SurfaceID: "view", SessionID: "sess-1", Generation: 7, Kind: protocol.UISurfaceView, Payload: mustRaw(t, map[string]any{"body": []map[string]any{{"kind": "text", "value": "once"}}})}
	publishRaw(t, h, "alpha", params)
	h.ClientCrashed("alpha")
	h.HandlerFor("alpha")
	if h.Surfaces()[0].Code != event.SurfaceExpired {
		t.Fatal("reconnecting revived an old publication")
	}
	publishRaw(t, h, "alpha", params)
	if h.Surfaces()[0].Code != "" {
		t.Fatal("a new accepted publication did not replace expiry")
	}
}
