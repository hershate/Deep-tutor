package boot

import (
	"context"
	"encoding/json"
	"testing"

	"reasonix/internal/contract/event"
	"reasonix/internal/ext/extension/protocol"
)

func TestEffectSurfacePublicationCanBeRecoveredThroughController(t *testing.T) {
	rec := &uiSinkRecorder{}
	res := bootWithFakeUIPlugin(t, "surface-replay", map[string]any{
		"capabilities": []string{"ui"},
		"env":          map[string]string{bootFakeEnvInitResult: `{"protocolVersion":"2","name":"surface-replay","version":"1.0.0","stateSchemaVersion":0}`},
	}, event.FuncSink(rec.emit))
	h := res.ExtensionUI
	payload := json.RawMessage(`{"body":[{"kind":"text","value":"retained view"},{"kind":"button","actionId":"inspect","label":"Inspect"}]}`)
	result, err := h.HandlerFor("surface-replay").Publish(context.Background(), protocol.UIPublishParams{
		SessionID: h.SessionID(), Generation: h.Generation(), SurfaceID: "view", Kind: protocol.UISurfaceView, Payload: payload,
	})
	if err != nil || !result.Accepted {
		t.Fatalf("publication: %+v %v", result, err)
	}
	snapshot := res.Controller.ExtensionSurfaces()
	if len(snapshot) != 1 || snapshot[0].View == nil || snapshot[0].View.Body[0].Value != "retained view" {
		t.Fatalf("controller snapshot: %+v", snapshot)
	}
	h.BindGeneration(h.SessionID(), h.Generation()+1)
	snapshot = res.Controller.ExtensionSurfaces()
	if len(snapshot) != 1 || snapshot[0].Code != event.SurfaceExpired || snapshot[0].View != nil {
		t.Fatalf("retired snapshot: %+v", snapshot)
	}
}
