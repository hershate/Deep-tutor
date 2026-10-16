package boot

import (
	"context"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"reasonix/internal/contract/event"

	"reasonix/internal/runtime/agent/testutil"
	"reasonix/internal/session/control"
)

type handbackSink struct{ done chan event.Event }

func (s *handbackSink) Emit(e event.Event) {
	if e.Kind == event.TurnDone {
		s.done <- e
	}
}

func TestEffectExplicitHandbackEndsTheTurnWithoutContinuation(t *testing.T) {
	write := call("src", "write_file", `{"path":"Program.cs","content":"class Q {}\n"}`)
	for _, tc := range []struct {
		name     string
		turns    []testutil.Turn
		requests int
	}{
		{name: "conclude_blocked", turns: []testutil.Turn{write,
			call("cb", "conclude_blocked", `{"blocker":"needs a key only the user has","evidence":[{"paths":["Program.cs"]}]}`)}, requests: 3},
		{name: "await_user", turns: []testutil.Turn{
			call("t0", "todo_write", `{"todos":[{"content":"Draft","status":"in_progress"},{"content":"Finish after review","status":"pending"}]}`),
			write,
			call("aw", "await_user", `{"need":"Please review the draft before I continue"}`)}, requests: 4},
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
			writeUserConfig(t, userModel+"\n[codegraph]\nenabled = false\n")
			registerBootTokenProfileTestProvider()
			writeFile(t, dir, "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk\" />\n")
			writeFile(t, dir, "Program.cs", "class P {}\n")
			approveWorkspace(t, dir)
			sink := &handbackSink{done: make(chan event.Event, 4)}
			prov := testutil.NewMock("handback", append(slices.Clone(tc.turns), testutil.Turn{Text: "handed back"}, testutil.Turn{Text: "again"}, testutil.Turn{Text: "again"})...)
			setBootTokenProfileTestProvider(t, prov)
			ctrl, err := Build(context.Background(), Options{Home: home, WorkspaceRoot: dir, AgentPreset: AgentPresetBalanced, Sink: sink, HeadlessApprovalMode: control.ToolApprovalAuto})
			if err != nil {
				t.Fatal(err)
			}
			defer ctrl.Close()
			ctrl.EnableInteractiveApproval()
			ctrl.SetToolApprovalMode(control.ToolApprovalAuto)
			ctrl.Submit("change the program")
			select {
			case ev := <-sink.done:
				if ev.Err != nil {
					t.Fatalf("turn ended with %v, want the explicit handback to end it cleanly", ev.Err)
				}
				if ev.Receipt == nil || !slices.ContainsFunc(ev.Receipt.Gaps, func(g event.ReceiptGap) bool { return g.Kind == "unverified_change" }) {
					t.Errorf("receipt = %+v, want the unverified change still reported", ev.Receipt)
				}
			case <-time.After(60 * time.Second):
				t.Fatal("turn did not finish")
			}
			if got := len(prov.Requests()); got != tc.requests {
				t.Errorf("provider requests = %d, want %d: the host continued a turn the model handed back", got, tc.requests)
			}
		})
	}
}
