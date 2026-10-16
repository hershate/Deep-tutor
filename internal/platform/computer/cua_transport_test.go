package computer

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

func TestCUAInitializesAndPreservesStructuredResult(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("REASONIX_CUA_FAKE", "1")
	h := NewDriver(exe, os.Environ())
	defer h.Close()
	var result DriverResult
	if err := h.Call(context.Background(), "list_apps", map[string]any{}, &result); err != nil {
		t.Fatal(err)
	}
	var apps struct {
		Apps []struct {
			PID int `json:"pid"`
		} `json:"apps"`
	}
	if err := json.Unmarshal(result.Structured, &apps); err != nil || len(apps.Apps) != 1 || apps.Apps[0].PID <= 0 {
		t.Fatalf("result: %+v %v", result, err)
	}
}

func TestCUAToolRefusalCannotBecomeSuccess(t *testing.T) {
	exe, _ := os.Executable()
	t.Setenv("REASONIX_CUA_FAKE", "1")
	h := NewDriver(exe, os.Environ())
	defer h.Close()
	var result DriverResult
	if err := h.Call(context.Background(), "click", map[string]any{"reject": true}, &result); err == nil {
		t.Fatal("refused action accepted")
	}
}
