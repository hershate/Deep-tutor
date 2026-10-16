package agent

import (
	"strings"
	"testing"

	"reasonix/internal/safety/evidence"
	"reasonix/internal/state/instruction"
)

func TestOwedChecksWaivedByTheUserLeaveAChangedTurnFree(t *testing.T) {
	check := instruction.VerifyCheck{Command: "go test ./...", SourcePath: "AGENTS.md", Line: 3}
	writer := evidence.Receipt{ToolName: "write_file", Success: true, Write: true, Paths: []string{"a.go"}}
	checkAfter := evidence.Receipt{ToolName: "bash", Success: true, Command: "go test ./..."}

	a := &Agent{task: taskRuntime{ledger: readinessLedger(checkAfter, writer)}, projectChecks: []instruction.VerifyCheck{check}}
	if a.ReadinessResult().Ready {
		t.Fatal("setup: a change with its project check run before it should be owed the check")
	}
	a.role = agentRole{waiveOwedChecks: true}
	if got := a.ReadinessResult(); !got.Ready {
		t.Fatalf("with the checks waived the turn is still held: %+v", got)
	}
}

func TestOwedChecksWaivedByTheUserStillAnswerForTheTurnsOwnPlan(t *testing.T) {
	writer := evidence.Receipt{ToolName: "write_file", Success: true, Write: true, Paths: []string{"a.go"}}
	todo := evidence.Receipt{ToolName: "todo_write", Success: true, Todos: []evidence.TodoItem{{Content: "edit", Status: "in_progress"}}}

	a := &Agent{task: taskRuntime{ledger: readinessLedger(writer, todo)}, role: agentRole{waiveOwedChecks: true}}
	got := a.ReadinessResult()
	if got.Ready || !strings.Contains(got.Reason, "incomplete items") {
		t.Fatalf("waiving the checks must not waive the turn's own unfinished plan: %+v", got)
	}
}
