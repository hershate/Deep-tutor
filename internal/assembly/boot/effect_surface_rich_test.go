package boot

import (
	"context"
	"encoding/json"
	"errors"
	"image"
	"image/png"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reasonix/internal/runtime/agent"
	"slices"
	"strings"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/state/sessionstore"
)

func TestEffectRichSurfaceChoicesReachProviderAndReplay(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/camera.png" {
			w.Header().Set("Content-Type", "image/png")
			_ = png.Encode(w, image.NewRGBA(image.Rect(0, 0, 2, 2)))
			return
		}
		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write([]byte(`<base href="/photos/"><img src="data:image/gif;base64,AA" data-src="../camera.png" alt="Camera front">`))
	}))
	defer server.Close()
	spec := `{"spec_version":1,"body":[{"kind":"map","key":"trip","label":"Trip","field":{"type":"multichoice","required":true,"options":[{"value":"park","label":"Park","location":{"latitude":35,"longitude":139}},{"value":"museum","label":"Museum","location":{"latitude":36,"longitude":140}}]}},{"kind":"comparison","key":"camera","label":"Camera","field":{"type":"choice","options":[{"value":"a","label":"Alpha","image":{"path":"images/camera.png","alt":"Camera front"},"price":100,"currency":"USD","facts":[{"label":"Weight","value":"300g"}]}]}},{"kind":"dashboard","label":"Visits","dataset":{"categories":["Mon","Tue"],"series":[{"id":"visits","label":"Visits","values":[4,8]}]}}]}`
	spec = strings.Replace(spec, `"alt":"Camera front"`, `"alt":"Camera front","source":"`+server.URL+`"`, 1)
	importArgs, _ := json.Marshal(map[string]string{"url": server.URL, "image_url": server.URL + "/camera.png", "path": "images/camera.png", "alt": "Camera front"})
	probe := &capabilityProbeProvider{calls: []string{
		`{"action":"inspect","capability_id":"tool:import_web_image"}`,
		chartCall(t, "tool:import_web_image", string(importArgs)),
		chartCall(t, "tool:render_surface", spec)}}
	prompts := make(chan event.Ask, 1)
	ctrl := buildChartAssembly(t, "rich-surface-effect", probe, dir, event.FuncSink(func(e event.Event) {
		if e.Kind == event.AskRequest {
			prompts <- e.Ask
		}
	}))
	t.Cleanup(ctrl.Close)
	ctrl.EnableInteractiveApproval()
	ctrl.SetAutoApproveTools(true)
	path := filepath.Join(dir, ".reasonix", "sessions", "rich.jsonl")
	ctrl.SetSessionPath(path)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- ctrl.Run(ctx, "plan the trip and compare cameras") }()
	const answer = `{"trip":["museum","park"],"camera":"a"}`
	select {
	case ask := <-prompts:
		if _, err := os.Stat(filepath.Join(dir, "images/camera.png")); err != nil {
			t.Fatalf("image not acquired before publication: %v; results: %v", err, probe.toolResults())
		}
		body := ask.Questions[0].Surface.Body
		if body[1].Field.Options[0].Image.Source != server.URL {
			t.Fatal("image source missing from frontend")
		}

		if body[0].Field.Options[0].Location == nil || body[1].Field.Options[0].Facts[0].Value != "300g" || body[1].Field.Options[0].Image == nil || body[1].Field.Options[0].Image.Path != "images/camera.png" || body[2].Dataset.Series[0].Values[1] != 8 {
			t.Fatal("rich data did not reach frontend")
		}
		if err := ctrl.AnswerQuestion(ask.ID, []event.AskAnswer{{QuestionID: "surface", Value: json.RawMessage(answer)}}); err != nil {
			t.Fatal(err)
		}
	case err := <-done:
		t.Fatalf("ended without surface: %v", err)
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	select {
	case err := <-done:
		var unready *agent.FinalReadinessError
		if !errors.As(err, &unready) || !slices.Contains(unready.Missing, "verification") {
			t.Fatalf("image write must retain verification debt: %v", err)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	assertRichAnswer(t, probe.toolResults())
	ctrl.Close()
	loaded, err := sessionstore.LoadSession(path)
	if err != nil {
		t.Fatal(err)
	}
	var results []string
	for _, m := range loaded.Snapshot() {
		if m.Role == provider.RoleTool {
			results = append(results, m.Content)
		}
	}
	assertRichAnswer(t, results)
	foundImage := false
	for _, result := range results {
		if strings.Contains(result, `"sha256"`) && strings.Contains(result, server.URL) {
			foundImage = true
		}
	}
	if !foundImage {
		t.Fatal("image retrieval provenance absent from replay")
	}
	probe.mu.Lock()
	defer probe.mu.Unlock()
	for _, schema := range probe.reqs[0].Tools {
		if schema.Name == "import_web_image" {
			t.Fatal("image tool leaked into provider prefix")
		}
	}

}

func assertRichAnswer(t *testing.T, results []string) {
	t.Helper()
	for _, result := range results {
		if strings.Contains(result, `"trip":["museum","park"]`) && strings.Contains(result, `"camera":"a"`) {
			return
		}
	}
	t.Fatal("ordered itinerary and chosen product absent")
}
