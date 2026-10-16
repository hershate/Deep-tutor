package serve

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

func historyGet(t *testing.T, base, query string) (int, map[string]any) {
	t.Helper()
	res, err := http.Get(base + "/workspace/git/log" + query)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(res.Body).Decode(&out)
	return res.StatusCode, out
}

func TestGitHistoryAnswersTheGraphOfTheWorkspaceRepository(t *testing.T) {
	dir := repoWith(t, map[string]string{"a.txt": "a\n"})
	gitIn(t, dir, "checkout", "-q", "-b", "side")
	gitIn(t, dir, "commit", "-q", "--allow-empty", "-m", "on the side")
	gitIn(t, dir, "checkout", "-q", "-")
	gitIn(t, dir, "merge", "-q", "--no-ff", "-m", "bring it in", "side")
	srv := commitServer(t, dir, nil)
	status, body := historyGet(t, srv.URL, "")
	commits, _ := body["commits"].([]any)
	if status != http.StatusOK || body["repo"] != true || len(commits) < 3 {
		t.Fatalf("history = %d %v", status, body)
	}
	tip, _ := commits[0].(map[string]any)
	if parents, _ := tip["parents"].([]any); tip["subject"] != "bring it in" || len(parents) != 2 {
		t.Fatalf("tip = %v, want the merge with two parents", tip)
	}
}

func TestGitHistoryBoundsAndSaysSo(t *testing.T) {
	dir := repoWith(t, map[string]string{"a.txt": "a\n"})
	for i := range 3 {
		gitIn(t, dir, "commit", "-q", "--allow-empty", "-m", strings.Repeat("c", i+1))
	}
	srv := commitServer(t, dir, nil)
	_, body := historyGet(t, srv.URL, "?limit=2")
	if commits, _ := body["commits"].([]any); len(commits) != 2 || body["truncated"] != true {
		t.Fatalf("limit=2 = %v, want two commits and truncated", body)
	}
	if status, _ := historyGet(t, srv.URL, "?limit=abc"); status != http.StatusBadRequest {
		t.Fatalf("a bad limit answered %d, want 400", status)
	}
}

func TestGitHistoryOfAWorkspaceWithoutGitIsQuiet(t *testing.T) {
	srv := commitServer(t, "", nil)
	status, body := historyGet(t, srv.URL, "")
	if commits, _ := body["commits"].([]any); status != http.StatusOK || body["repo"] != false || len(commits) != 0 {
		t.Fatalf("no repository = %d %v, want 200, repo false, no commits", status, body)
	}
}
