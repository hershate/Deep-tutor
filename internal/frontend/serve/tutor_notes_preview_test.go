package serve

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

// The notes list carries a preview of the body, the detail endpoint carries
// the full text. The note ceiling is 2000 notes of up to 20000 runes each:
// a list that carried every body would marshal a hundred megabytes into one
// response — a memory spike per request on a network-exposed surface.
func TestTutorNotesListCarriesAPreview(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)
	body := strings.Repeat("学", 900)
	resp, raw := tutorPost(t, srv, "/tutor/notes", map[string]string{"title": "长笔记", "body": body})
	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusOK {
		t.Fatalf("create note: %d %s", resp.StatusCode, raw)
	}
	var created struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(raw), &created); err != nil || created.ID == "" {
		t.Fatalf("no note id in %s (%v)", raw, err)
	}

	resp, raw = tutorGet(t, srv, "/tutor/notes")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("list notes: %d %s", resp.StatusCode, raw)
	}
	var list []struct {
		ID        string `json:"id"`
		Body      string `json:"body"`
		Truncated bool   `json:"truncated"`
	}
	if err := json.Unmarshal([]byte(raw), &list); err != nil {
		t.Fatalf("decode the list: %v (%s)", err, raw)
	}
	if len(list) != 1 {
		t.Fatalf("the list holds %d notes, want 1", len(list))
	}
	if len([]rune(list[0].Body)) != notePreviewRunes {
		t.Fatalf("the list carried %d runes, want %d", len([]rune(list[0].Body)), notePreviewRunes)
	}
	if !list[0].Truncated {
		t.Fatal("the list did not say the body was cut")
	}

	// The detail endpoint still carries the whole text.
	resp, raw = tutorGet(t, srv, "/tutor/notes/"+created.ID)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("read the note: %d %s", resp.StatusCode, raw)
	}
	var detail struct {
		Body string `json:"body"`
	}
	if err := json.Unmarshal([]byte(raw), &detail); err != nil {
		t.Fatalf("decode the note: %v (%s)", err, raw)
	}
	if detail.Body != body {
		t.Fatalf("the detail carried %d runes, want %d", len([]rune(detail.Body)), len([]rune(body)))
	}
}

// A short note is not cut and says so.
func TestTutorNotesListKeepsAShortBodyWhole(t *testing.T) {
	_, srv, _ := newTutorTestServer(t)
	resp, raw := tutorPost(t, srv, "/tutor/notes", map[string]string{"title": "短笔记", "body": "只有一行"})
	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusOK {
		t.Fatalf("create note: %d %s", resp.StatusCode, raw)
	}
	resp, raw = tutorGet(t, srv, "/tutor/notes")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("list notes: %d %s", resp.StatusCode, raw)
	}
	var list []struct {
		Body      string `json:"body"`
		Truncated bool   `json:"truncated"`
	}
	if err := json.Unmarshal([]byte(raw), &list); err != nil {
		t.Fatalf("decode the list: %v (%s)", err, raw)
	}
	if len(list) != 1 {
		t.Fatalf("the list holds %d notes, want 1", len(list))
	}
	if list[0].Truncated {
		t.Fatal("a short body was reported as cut")
	}
	if list[0].Body != "只有一行" {
		t.Fatalf("the list carried %q", list[0].Body)
	}
}
