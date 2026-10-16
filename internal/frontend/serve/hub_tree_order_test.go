package serve

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"sync"
	"testing"

	"reasonix/internal/base/testenv"
)

func TestWorkspaceMoveKeepsNoPaneDefaultProject(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	a, b := testenv.TempDir(t), testenv.TempDir(t)
	rememberWorkspace(a)
	rememberWorkspace(b)
	if err := moveWorkspace(context.Background(), b, 1); err != nil {
		t.Fatal(err)
	}
	h := NewHub(HubOptions{})
	if got, err := h.resolveRoot(OpenRequest{}); got != b || err != nil {
		t.Fatalf("no-pane default after reorder = %q (%v), want launch project %q", got, err, b)
	}
	if err := os.Remove(b); err != nil {
		t.Fatal(err)
	}
	if got, err := h.resolveRoot(OpenRequest{}); got != a || err != nil {
		t.Fatalf("missing launch project fallback = %q (%v), want %q", got, err, a)
	}
}

func TestWorkspaceMovePersistsOrderWithOpenPanes(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	a, b, c := testenv.TempDir(t), testenv.TempDir(t), testenv.TempDir(t)
	for _, dir := range []string{a, b, c} {
		rememberWorkspace(dir)
	}
	h := NewHub(HubOptions{})
	hubRuntime(t, h, a)
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	body, _ := json.Marshal(map[string]any{"path": c, "direction": 1})
	status, code := postLaunchJSON(t, srv.URL+"/tree/workspaces/move", string(body), nil)
	if status != http.StatusNoContent {
		t.Fatalf("move = %d %s, want 204", status, code)
	}
	want := []string{b, c, a}
	if got := Workspaces(); !slices.Equal(got, want) {
		t.Fatalf("saved order = %v, want %v", got, want)
	}
	if launch := LaunchWorkspaces(); len(launch) == 0 || launch[0] != c {
		t.Fatalf("moving changed the launch project: %v", launch)
	}
	rows := hubGet[[]treeWorkspace](t, srv, "/tree")
	var got []string
	for _, row := range rows {
		got = append(got, row.Root)
	}
	if !slices.Equal(got, want) {
		t.Fatalf("tree order = %v, want %v", got, want)
	}
	data, err := os.ReadFile(workspacesPath())
	if err != nil || !json.Valid(data) {
		t.Fatalf("saved workspace file: %s (%v)", data, err)
	}
	other := NewHub(HubOptions{})
	if refs := other.roots(); len(refs) != 3 || refs[0].dir != b || refs[1].dir != c || refs[2].dir != a {
		t.Fatalf("new hub did not restore the order: %v", refs)
	}
}

func TestWorkspaceMoveLegacyListAndValidation(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	a, b := testenv.TempDir(t), testenv.TempDir(t)
	data, _ := json.Marshal([]string{a, b})
	if err := os.WriteFile(workspacesPath(), data, 0o644); err != nil {
		t.Fatal(err)
	}
	h := NewHub(HubOptions{})
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	for _, tc := range []struct {
		path      string
		direction int
		status    int
		code      string
	}{
		{"", 1, 400, "request.missing_field"},
		{a, 0, 400, "request.bad_value"},
		{a, 2, 400, "request.bad_value"},
		{"unknown", 1, 404, "request.not_found"},
		{a, -1, 204, ""},
		{b, 1, 204, ""},
		{b, -1, 204, ""},
	} {
		body, _ := json.Marshal(map[string]any{"path": tc.path, "direction": tc.direction})
		status, code := postLaunchJSON(t, srv.URL+"/tree/workspaces/move", string(body), nil)
		if status != tc.status || code != tc.code {
			t.Fatalf("move %q %d = %d %s, want %d %s", tc.path, tc.direction, status, code, tc.status, tc.code)
		}
	}
	if !slices.Equal(Workspaces(), []string{b, a}) || !slices.Equal(LaunchWorkspaces(), []string{a, b}) {
		t.Fatalf("legacy launch was lost: tree %v, launch %v", Workspaces(), LaunchWorkspaces())
	}
	rememberWorkspace(a)
	if !slices.Equal(Workspaces(), []string{b, a}) || LaunchWorkspaces()[0] != a {
		t.Fatal("reopening a remembered project changed its order or launch project")
	}
	forgetWorkspace(a)
	if !slices.Equal(LaunchWorkspaces(), []string{b}) {
		t.Fatalf("removed launch project retained: %v", LaunchWorkspaces())
	}
}

func TestWorkspaceMoveRefusesUnauthenticatedMutation(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	h := NewHub(HubOptions{})
	srv := httptest.NewServer(h.Handler())
	defer srv.Close()
	status, code := postLaunchJSON(t, srv.URL+"/tree/workspaces/move", `{"path":"/a","direction":1}`, nil)
	if status != 403 || code != "auth.launch_token_required" {
		t.Fatalf("unauthenticated move = %d %s", status, code)
	}
}

func TestWorkspaceListConcurrentUpdatesAndWriteFailure(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	ctx, cancel := context.WithTimeout(context.Background(), testenv.Budget(t))
	defer cancel()
	errs := make(chan error, 20)
	var wg sync.WaitGroup
	for range 20 {
		dir := testenv.TempDir(t)
		wg.Go(func() { errs <- addRememberedWorkspace(ctx, dir) })
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatalf("remember workspace: %v", err)
		}
	}
	if len(Workspaces()) != 20 {
		t.Fatalf("concurrent additions lost: %v", Workspaces())
	}
	path := workspacesPath()
	data := []byte("invalid workspace list")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
	h := NewHub(HubOptions{})
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	status, _ := postLaunchJSON(t, srv.URL+"/tree/workspaces/move", `{"path":"/a","direction":1}`, nil)
	if status != 500 {
		t.Fatalf("unreadable workspace list reported success: %d", status)
	}
	got, err := os.ReadFile(path)
	if err != nil || !slices.Equal(got, data) {
		t.Fatalf("unreadable list overwritten: %s (%v)", got, err)
	}
}

func TestWorkspaceAddAtCapacityRefusesWithoutDroppingManuallyOrderedPaths(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	for i := range workspaceRecentMax {
		rememberWorkspace(fmt.Sprintf("/project-%d", i))
	}
	paths := Workspaces()
	if err := moveWorkspace(t.Context(), paths[0], 1); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(workspacesPath())
	if err != nil {
		t.Fatal(err)
	}
	h := NewHub(HubOptions{})
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	body, _ := json.Marshal(map[string]string{"path": testenv.TempDir(t)})
	status, code := postLaunchJSON(t, srv.URL+"/tree/workspaces", string(body), nil)
	if status != http.StatusConflict || code != "workspace.limit_reached" {
		t.Fatalf("add at capacity=%d %s", status, code)
	}
	after, err := os.ReadFile(workspacesPath())
	if err != nil || !slices.Equal(before, after) {
		t.Fatal("capacity refusal changed existing project order")
	}
}

func TestWorkspaceRememberAndForgetRepairMalformedJSON(t *testing.T) {
	for _, malformed := range []string{"corrupted JSON", `{"paths":42}`} {
		for _, action := range []string{"remember", "forget"} {
			t.Run(action+"/"+malformed, func(t *testing.T) {
				t.Setenv("REASONIX_HOME", testenv.TempDir(t))
				path := workspacesPath()
				if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte(malformed), 0o644); err != nil {
					t.Fatal(err)
				}
				if action == "remember" {
					rememberWorkspace("/new")
				} else {
					forgetWorkspace("/old")
				}
				body, err := os.ReadFile(path)
				if err != nil || !json.Valid(body) {
					t.Fatalf("malformed list was not repaired: %q (%v)", body, err)
				}
				backups, err := filepath.Glob(path + ".bak-*")
				if err != nil || len(backups) != 1 {
					t.Fatalf("repair backup = %v, %v", backups, err)
				}
				original, err := os.ReadFile(backups[0])
				if err != nil || string(original) != malformed {
					t.Fatalf("repair lost original bytes: %q, %v", original, err)
				}
				if action == "remember" && !slices.Equal(Workspaces(), []string{"/new"}) {
					t.Fatalf("repaired paths=%v", Workspaces())
				}
			})
		}
	}
}

func TestWorkspaceRepairPreservesReadFailures(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	path := workspacesPath()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := addRememberedWorkspace(context.Background(), "/new"); err == nil {
		t.Fatal("reading a directory reported success")
	}
	forgetWorkspace("/old")
	if info, err := os.Stat(path); err != nil || !info.IsDir() {
		t.Fatalf("read failure replaced the source directory: %v (%v)", info, err)
	}
}

// Dragging a project to a place names where it lands, not how many steps to
// take: the order after the call is the one the person dropped.
func TestWorkspacePlaceLandsBeforeTheNamedProjectOrAtTheEnd(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	a, b, c, d := testenv.TempDir(t), testenv.TempDir(t), testenv.TempDir(t), testenv.TempDir(t)
	for _, dir := range []string{a, b, c, d} {
		rememberWorkspace(dir)
	}
	// rememberWorkspace puts a new folder first, so the saved order is d c b a.
	h := NewHub(HubOptions{})
	srv := httptest.NewServer(operatorHandler(h))
	defer srv.Close()
	place := func(path string, before any) (int, string) {
		body, _ := json.Marshal(map[string]any{"path": path, "before": before})
		return postLaunchJSON(t, srv.URL+"/tree/workspaces/move", string(body), nil)
	}
	launch := LaunchWorkspaces()[0]
	for _, tc := range []struct {
		name   string
		path   string
		before any
		want   []string
	}{
		{"to the front", a, d, []string{a, d, c, b}},
		{"into the middle", d, b, []string{a, c, d, b}},
		{"to the end", a, "", []string{c, d, b, a}},
		{"before itself changes nothing", c, c, []string{c, d, b, a}},
		{"before the project it already precedes changes nothing", c, d, []string{c, d, b, a}},
	} {
		if status, code := place(tc.path, tc.before); status != http.StatusNoContent {
			t.Fatalf("%s = %d %s, want 204", tc.name, status, code)
		}
		if got := Workspaces(); !slices.Equal(got, tc.want) {
			t.Fatalf("%s: order = %v, want %v", tc.name, got, tc.want)
		}
	}
	if got := LaunchWorkspaces()[0]; got != launch {
		t.Fatalf("dropping a project changed the launch project: %s then %s", launch, got)
	}
	for _, tc := range []struct {
		path   string
		before any
		status int
		code   string
	}{
		{"unknown", a, 404, "request.not_found"},
		{a, "unknown", 404, "request.not_found"},
		{"", a, 400, "request.missing_field"},
	} {
		if status, code := place(tc.path, tc.before); status != tc.status || code != tc.code {
			t.Fatalf("place %q before %v = %d %s, want %d %s", tc.path, tc.before, status, code, tc.status, tc.code)
		}
	}
}
