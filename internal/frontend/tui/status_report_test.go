package tui

import (
	"testing"

	"reasonix/internal/frontend/termrender"
)

func TestStatusIsReportedOnlyWhenItChanges(t *testing.T) {
	m, _ := testModel(t)
	m.reportOn = true
	if got := m.reportStatus(); got == nil {
		t.Fatal("the first state was not reported")
	}
	if got := m.reportStatus(); got != nil {
		t.Fatal("an unchanged state was reported again")
	}
	m.tr.Running = true
	if m.reportStatus() == nil {
		t.Fatal("a running turn was not reported")
	}
	if state, _ := m.terminalStatus(); state != termrender.StateWorking {
		t.Fatalf("state = %s, want working", state)
	}
	m.tr.Running = false
	if state, _ := m.terminalStatus(); state != termrender.StateDone {
		t.Fatalf("state after a turn = %s, want done", state)
	}
}

func TestAPendingApprovalBlocksAndAnAskIsAQuestion(t *testing.T) {
	m, _ := testModel(t)
	m.tr.Running = true
	m.tr.Items = append(m.tr.Items, Item{Kind: ItemApproval})
	if s, k := m.terminalStatus(); s != termrender.StateBlocked || k != termrender.BlockedPermission {
		t.Fatalf("approval -> %s/%s", s, k)
	}
	m.tr.Items = []Item{{Kind: ItemAsk}}
	if s, k := m.terminalStatus(); s != termrender.StateBlocked || k != termrender.BlockedQuestion {
		t.Fatalf("ask -> %s/%s", s, k)
	}
}

func TestNothingIsReportedWhenStdoutIsNotATerminal(t *testing.T) {
	m, _ := testModel(t)
	m.tr.Running = true
	if m.reportStatus() != nil {
		t.Fatal("a status record was emitted without a terminal")
	}
}
