package boot

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"reasonix/internal/runtime/agent"
	"reasonix/internal/runtime/agent/testutil"
	"reasonix/internal/session/control"
)

func TestEffectHostVerificationOffIsTheUsersChoiceOnly(t *testing.T) {
	for _, tc := range []struct {
		name        string
		userExtra   string
		projectFile string
		wantHeld    bool
	}{
		{name: "default holds a changed turn to its declared check", wantHeld: true},
		{name: "the user file turns it off", userExtra: "\n[agent]\nhost_verification = \"off\"\n"},
		{name: "a project file cannot turn it off", projectFile: "[agent]\nhost_verification = \"off\"\n", wantHeld: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			isolateConfigHome(t)
			home, err := filepath.EvalSymlinks(robustTempDir(t))
			if err != nil {
				t.Fatal(err)
			}
			t.Setenv("REASONIX_HOME", home)
			dir, err := filepath.EvalSymlinks(robustTempDir(t))
			if err != nil {
				t.Fatal(err)
			}
			t.Chdir(dir)
			writeUserConfig(t, userModel+"\n[codegraph]\nenabled = false\n"+tc.userExtra)
			registerBootTokenProfileTestProvider()
			writeFile(t, dir, "AGENTS.md", "## Reasonix host checks\n\n- verify: go test ./...\n")
			if tc.projectFile != "" {
				writeFile(t, dir, "reasonix.toml", tc.projectFile)
			}
			approveWorkspace(t, dir)
			prov := testutil.NewMock("host-verification",
				call("notes", "write_file", `{"path":"notes.md","content":"A neutral note.\n"}`),
				testutil.Turn{Text: "done"},
			)
			setBootTokenProfileTestProvider(t, prov)
			ctrl, err := Build(context.Background(), Options{Home: home, WorkspaceRoot: dir, AgentPreset: AgentPresetBalanced, HeadlessApprovalMode: control.ToolApprovalAuto})
			if err != nil {
				t.Fatal(err)
			}
			defer ctrl.Close()
			err = ctrl.Run(context.Background(), "write the note")
			var unready *agent.FinalReadinessError
			if held := errors.As(err, &unready); held != tc.wantHeld {
				t.Fatalf("Run = %v, held = %v, want held = %v", err, held, tc.wantHeld)
			}
			if !tc.wantHeld && err != nil {
				t.Fatalf("Run = %v, want completion", err)
			}
			if _, statErr := os.Stat(filepath.Join(dir, "notes.md")); statErr != nil {
				t.Fatal(statErr)
			}
		})
	}
}
