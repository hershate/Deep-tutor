package serve

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/session/control"
)

func branchAtCode(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	var reason Reason
	if err := json.NewDecoder(rec.Body).Decode(&reason); err != nil {
		t.Fatalf("decode refusal: %v", err)
	}
	return reason.Code
}

// What a frontend has to tell apart: not now, not here, and broken.
func TestBranchAtRefusalsCarryAnIdentity(t *testing.T) {
	for _, tc := range []struct {
		err    error
		status int
		code   string
	}{
		{fmt.Errorf("busy: %w", control.ErrTurnRunning), http.StatusConflict, "conversation_branch.turn_running"},
		{fmt.Errorf("no boundary: %w", control.ErrBranchUnavailable), http.StatusConflict, "conversation_branch.unavailable"},
		{errors.New("disk full"), http.StatusInternalServerError, "conversation_branch.failed"},
	} {
		rec := httptest.NewRecorder()
		refuseBranchAt(rec, tc.err)
		if rec.Code != tc.status || branchAtCode(t, rec) != tc.code {
			t.Errorf("%v -> %d, want %d %s", tc.err, rec.Code, tc.status, tc.code)
		}
	}
}

func TestBranchAtNeedsATurn(t *testing.T) {
	ctrl := control.New(control.Options{WorkspaceRoot: testenv.TempDir(t), SessionPath: filepath.Join(testenv.TempDir(t), "s.jsonl")})
	defer ctrl.Close()
	rec := httptest.NewRecorder()
	(&Server{ctrl: ctrl}).branchAt(rec, httptest.NewRequest(http.MethodPost, "/branch/at", strings.NewReader(`{"name":"x"}`)))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("a request with no turn = %d, want 400", rec.Code)
	}
}

func TestBranchAtASessionThatCannotBranchIsRefusedNotBroken(t *testing.T) {
	ctrl := control.New(control.Options{WorkspaceRoot: testenv.TempDir(t), SessionPath: filepath.Join(testenv.TempDir(t), "s.jsonl")})
	defer ctrl.Close()
	rec := httptest.NewRecorder()
	(&Server{ctrl: ctrl}).branchAt(rec, httptest.NewRequest(http.MethodPost, "/branch/at", strings.NewReader(`{"turn":1}`)))
	if rec.Code != http.StatusConflict || branchAtCode(t, rec) != "conversation_branch.unavailable" {
		t.Fatalf("a controller with no conversation = %d, want a branch.unavailable refusal", rec.Code)
	}
}
