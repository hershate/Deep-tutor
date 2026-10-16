package configbackup

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"slices"
	"testing"

	"reasonix/internal/ext/hook"
)

func TestHookBackupVariantsRoundTrip(t *testing.T) {
	machine(t, "")
	base := hook.HookConfig{Match: "edit_file", Command: "echo check", Cwd: "/tmp/project-a"}
	directory, timeout, description := base, base, base
	directory.Cwd = "/tmp/project-b"
	timeout.Timeout = 1234
	description.Description = "another label"
	want := []hook.HookConfig{base, directory, timeout, description}
	must(t, hook.Save(hook.ScopeGlobal, "", hook.Settings{Hooks: map[hook.Event][]hook.HookConfig{hook.PostToolUse: want}}))
	s := collectAll(t, CategoryAutomation)
	if s.Format < 2 {
		t.Fatalf("distinct hook identities need snapshot format 2 or newer, got %d", s.Format)
	}
	sealed, err := Seal(s, passphrase)
	must(t, err)
	s, err = Open(sealed, passphrase)
	must(t, err)
	if len(s.Items) != len(want) {
		t.Fatalf("collected %d hooks, want %d", len(s.Items), len(want))
	}
	machine(t, "")
	for range 2 {
		p := NewPlanner()
		plan, err := p.Preview(s)
		must(t, err)
		var ids []string
		for _, it := range plan.Items {
			ids = append(ids, it.ID)
		}
		result, err := p.Apply(ApplyRequest{PlanID: plan.ID, Items: ids, Consented: ids})
		must(t, err)
		if len(result.Failed) != 0 {
			t.Fatalf("restore failed: %+v", result)
		}
		got, err := globalHooks()
		must(t, err)
		raw, err := json.Marshal(got.Hooks[hook.PostToolUse])
		must(t, err)
		wantRaw, err := json.Marshal(want)
		must(t, err)
		if string(raw) != string(wantRaw) {
			t.Fatalf("restored hooks = %s, want %s", raw, wantRaw)
		}
	}
	plan, err := NewPlanner().Preview(s)
	must(t, err)
	for _, it := range plan.Items {
		if it.Status != StatusSame {
			t.Fatalf("restored hook is not the same: %+v", it)
		}
	}
	for _, detail := range []string{"timeout: 1234 ms", "description: another label"} {
		if !slices.ContainsFunc(plan.Items, func(row PlanItem) bool { return slices.Contains(row.Details, detail) }) {
			t.Fatalf("preview omits %q", detail)
		}
	}
}

func TestHookPreviewMatchesNativeImportIdentity(t *testing.T) {
	for _, tc := range []struct {
		name        string
		env         map[string]string
		contextFile string
		secrets     bool
	}{
		{"placeholder env", map[string]string{"TOKEN": "${SOURCE_TOKEN}"}, "", true},
		{"redacted env", map[string]string{"OTHER": "incoming-secret"}, "", false},
		{"context file", nil, "/incoming/context.md", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			machine(t, "")
			h := hook.HookConfig{Match: "edit_file", Command: "echo check", Env: tc.env, ContextFile: tc.contextFile}
			writeSettings := func(h hook.HookConfig) []byte {
				raw, err := json.Marshal(hook.Settings{Hooks: map[hook.Event][]hook.HookConfig{hook.PostToolUse: {h}}})
				must(t, err)
				must(t, os.WriteFile(hook.GlobalSettingsPath(""), raw, 0o600))
				return raw
			}
			writeSettings(h)
			cats := []Category{CategoryAutomation}
			if tc.secrets {
				cats = append(cats, CategorySecrets)
			}
			s := collectAll(t, cats...)
			sealed, err := Seal(s, passphrase)
			must(t, err)
			s, err = Open(sealed, passphrase)
			must(t, err)
			if tc.env != nil {
				h.Env = map[string]string{"TOKEN": "local-secret"}
			}
			if tc.contextFile != "" {
				h.ContextFile = "/local/context.md"
			}
			before := writeSettings(h)
			for range 2 {
				p := NewPlanner()
				plan, err := p.Preview(s)
				must(t, err)
				row := planRow(t, plan, s.Items[0].ID)
				if row.Status != StatusSame || row.Recommended || row.Consent != ConsentExecutes {
					t.Errorf("native-equivalent hook preview: %+v", row)
				}
				ids := []string{row.ID}
				if _, err := p.Apply(ApplyRequest{PlanID: plan.ID, Items: ids}); !errors.Is(err, ErrConsentRequired) {
					t.Fatalf("missing consent: %v", err)
				}
				refused, err := os.ReadFile(hook.GlobalSettingsPath(""))
				must(t, err)
				if !bytes.Equal(before, refused) {
					t.Fatal("consent refusal rewrote local settings")
				}
				result, err := p.Apply(ApplyRequest{PlanID: plan.ID, Items: ids, Consented: ids})
				must(t, err)
				if len(result.Failed) != 0 || !slices.Equal(result.Applied, ids) {
					t.Fatalf("apply: %+v", result)
				}
				after, err := os.ReadFile(hook.GlobalSettingsPath(""))
				must(t, err)
				if !bytes.Equal(before, after) {
					t.Fatal("native-equivalent import rewrote local settings")
				}
			}
		})
	}
}

func TestHookBackupDeduplicatesIdenticalHooks(t *testing.T) {
	machine(t, "")
	h := hook.HookConfig{Command: "echo check"}
	must(t, hook.Save(hook.ScopeGlobal, "", hook.Settings{Hooks: map[hook.Event][]hook.HookConfig{hook.PostToolUse: {h, h}}}))
	loaded := hook.Load(hook.LoadOptions{Scopes: []hook.Scope{hook.ScopeGlobal}})
	calls := 0
	hook.Run(context.Background(), hook.Payload{Event: hook.PostToolUse}, loaded, func(context.Context, hook.SpawnInput) hook.SpawnResult {
		calls++
		return hook.SpawnResult{}
	})
	if calls != 2 {
		t.Fatalf("configured duplicate hooks ran %d times, want 2", calls)
	}
	s := collectAll(t, CategoryAutomation)
	must(t, s.validate())
	if len(s.Items) != 1 {
		t.Fatalf("identical hooks produced %d restore items", len(s.Items))
	}
}

func TestLegacyHookBackupPreviewAndImport(t *testing.T) {
	machine(t, "")
	h := hook.HookConfig{Match: "edit_file", Command: "echo check", Cwd: " /tmp/project-a ", Description: " check files ", Timeout: 1234}
	must(t, hook.Save(hook.ScopeGlobal, "", hook.Settings{Hooks: map[hook.Event][]hook.HookConfig{hook.PostToolUse: {h}}}))
	s := &Snapshot{Format: 1, Categories: []Category{CategoryAutomation}, Items: []Item{
		fileItem(t, KindHook, "PostToolUse#d0b85885ddd6", hookData{Event: string(hook.PostToolUse), Hook: h}),
	}}
	sealed, err := Seal(s, passphrase)
	must(t, err)
	s, err = Open(sealed, passphrase)
	must(t, err)
	for range 2 {
		p := NewPlanner()
		plan, err := p.Preview(s)
		must(t, err)
		row := planRow(t, plan, s.Items[0].ID)
		if row.Status != StatusSame {
			t.Fatalf("legacy backup misidentified: %+v", row)
		}
		if _, err := p.Apply(ApplyRequest{PlanID: plan.ID, Items: []string{row.ID}}); !errors.Is(err, ErrConsentRequired) {
			t.Fatalf("legacy hook without consent: %v", err)
		}
		result, err := p.Apply(ApplyRequest{PlanID: plan.ID, Items: []string{row.ID}, Consented: []string{row.ID}})
		must(t, err)
		if len(result.Failed) != 0 {
			t.Fatalf("legacy restore failed: %+v", result)
		}
		got, err := globalHooks()
		must(t, err)
		if len(got.Hooks[hook.PostToolUse]) != 1 {
			t.Fatalf("legacy restore duplicated hook: %+v", got)
		}
	}
}

func TestHookBackupEnvironmentIdentity(t *testing.T) {
	machine(t, "")
	settings := hook.Settings{Hooks: map[hook.Event][]hook.HookConfig{hook.PostToolUse: {
		{Command: "echo check", Env: map[string]string{"TOKEN": "one"}},
		{Command: "echo check", Env: map[string]string{"TOKEN": "two"}},
		{Command: "echo check", Env: map[string]string{"OTHER": "one"}},
		{Command: "echo check", ContextFile: "/tmp/context.md"},
	}}}
	raw, err := json.Marshal(settings)
	must(t, err)
	for _, secrets := range []bool{false, true} {
		must(t, os.WriteFile(hook.GlobalSettingsPath(""), raw, 0o600))
		cats := []Category{CategoryAutomation}
		want := 3
		if secrets {
			cats = append(cats, CategorySecrets)
			want = 4
		}
		s := collectAll(t, cats...)
		must(t, s.validate())
		if len(s.Items) != want {
			t.Fatalf("secrets=%v: got %d hook identities, want %d", secrets, len(s.Items), want)
		}
		plan, err := NewPlanner().Preview(s)
		must(t, err)
		for _, row := range plan.Items {
			if row.Status != StatusSame {
				t.Fatalf("secrets=%v: unchanged hook differs: %+v", secrets, row)
			}
		}
		machine(t, "")
		for range 2 {
			p := NewPlanner()
			plan, err := p.Preview(s)
			must(t, err)
			var ids []string
			for _, it := range plan.Items {
				ids = append(ids, it.ID)
			}
			result, err := p.Apply(ApplyRequest{PlanID: plan.ID, Items: ids, Consented: ids})
			must(t, err)
			if len(result.Failed) != 0 {
				t.Fatalf("restore failed: %+v", result)
			}
			got, err := globalHooks()
			must(t, err)
			if len(got.Hooks[hook.PostToolUse]) != 1 {
				t.Fatalf("unsupported env/context fields duplicated native hook: %+v", got)
			}
		}
	}
}

func TestHookBackupNormalizedFieldsStayIdempotent(t *testing.T) {
	for _, field := range []string{"cwd", "description"} {
		t.Run(field, func(t *testing.T) {
			machine(t, "")
			h := hook.HookConfig{Command: "echo check"}
			if field == "cwd" {
				h.Cwd = " /tmp/project-a "
			} else {
				h.Description = " check files "
			}
			raw, err := json.Marshal(hook.Settings{Hooks: map[hook.Event][]hook.HookConfig{hook.PostToolUse: {h}}})
			must(t, err)
			must(t, os.WriteFile(hook.GlobalSettingsPath(""), raw, 0600))
			s := collectAll(t, CategoryAutomation)
			sealed, err := Seal(s, passphrase)
			must(t, err)
			s, err = Open(sealed, passphrase)
			must(t, err)
			machine(t, "")
			for attempt := range 2 {
				p := NewPlanner()
				plan, err := p.Preview(s)
				must(t, err)
				if attempt > 0 && plan.Items[0].Status != StatusSame {
					t.Fatalf("normalized hook not recognized after import: %+v", plan.Items[0])
				}
				ids := []string{plan.Items[0].ID}
				result, err := p.Apply(ApplyRequest{PlanID: plan.ID, Items: ids, Consented: ids})
				must(t, err)
				if len(result.Failed) != 0 {
					t.Fatalf("import failed: %+v", result)
				}
			}
			got, err := globalHooks()
			must(t, err)
			if len(got.Hooks[hook.PostToolUse]) != 1 {
				t.Fatalf("repeat-import grew normalized hooks: %+v", got.Hooks[hook.PostToolUse])
			}
		})
	}
}
