package boot

import (
	"context"
	"errors"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"reasonix/internal/runtime/agent"
	"reasonix/internal/runtime/agent/testutil"
	"reasonix/internal/session/control"
)

// A command the classifier cannot read stands in for the check only while the
// host can say it changed nothing. `echo … > file` is graded from the redirect
// itself, so the host watched this one write and the code change it followed is
// still unverified; `ls` changes nothing and keeps its waiver.
func TestEffectAnUnrecognizedWriteStillOwesTheCheck(t *testing.T) {
	for _, tc := range []struct {
		name  string
		turns []testutil.Turn
		debt  bool
	}{
		{
			name: "a write the host watched",
			turns: []testutil.Turn{
				call("src", "write_file", `{"path":"main.go","content":"package main\n\nfunc main() {}\n"}`),
				call("gen", "bash", `{"command":"echo generated > generated.txt"}`),
			},
			debt: true,
		},
		{
			// The waiver asks about every unrecognized command, so a quiet one
			// after the write cannot stand in for the one that wrote.
			name: "a quiet command after the write the host watched",
			turns: []testutil.Turn{
				call("src", "write_file", `{"path":"main.go","content":"package main\n\nfunc main() {}\n"}`),
				call("gen", "bash", `{"command":"echo generated > generated.txt"}`),
				call("look", "bash", `{"command":"ls"}`),
			},
			debt: true,
		},
		{
			name: "a command that changed nothing",
			turns: []testutil.Turn{
				call("src", "write_file", `{"path":"main.go","content":"package main\n\nfunc main() {}\n"}`),
				call("look", "bash", `{"command":"ls"}`),
			},
		},
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
			// The turn's commands have to reach a shell for the waiver to be
			// about what they changed; a runner without bwrap refuses to run
			// them at all, and the turn would owe the check for another reason.
			writeUserConfig(t, userModel+"\n[sandbox]\nbash = \"off\"\n\n[codegraph]\nenabled = false\n")
			registerBootTokenProfileTestProvider()
			writeFile(t, dir, "go.mod", "module demo\n\ngo 1.26\n")
			writeFile(t, dir, "main.go", "package main\n")
			approveWorkspace(t, dir)
			prov := testutil.NewMock("unrecognized-write", append(slices.Clone(tc.turns), testutil.Turn{Text: "done"})...)
			setBootTokenProfileTestProvider(t, prov)
			ctrl, err := Build(context.Background(), Options{
				Home: home, WorkspaceRoot: dir, AgentPreset: AgentPresetBalanced,
				Sink: &bundleAuditSink{}, HeadlessApprovalMode: control.ToolApprovalAuto,
			})
			if err != nil {
				t.Fatal(err)
			}
			defer ctrl.Close()

			err = ctrl.Run(context.Background(), "add a main function")
			var unready *agent.FinalReadinessError
			if !tc.debt {
				if err != nil {
					t.Fatalf("Run = %v, want completion", err)
				}
				return
			}
			if !errors.As(err, &unready) || !slices.Contains(unready.Missing, "verification") {
				t.Fatalf("Run = %v, want a verification readiness error", err)
			}
			if !strings.Contains(unready.Reason, "run a relevant verification command") {
				t.Errorf("reason %q does not carry the ask", unready.Reason)
			}
		})
	}
}
