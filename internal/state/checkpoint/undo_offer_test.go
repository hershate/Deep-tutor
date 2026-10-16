package checkpoint

import "testing"

func TestUndoOfferChangeLifecycle(t *testing.T) {
	for _, action := range []string{"undo", "begin", "mutation", "invalidate"} {
		t.Run(action, func(t *testing.T) {
			s, _, _, path := twoOwnedTurns(t)
			var offers []*RewindUndo
			s.OnUndoChange(func() {
				offer, err := s.AvailableUndo()
				if err != nil {
					t.Error(err)
				}
				offers = append(offers, offer)
			})
			result := commitOwnedRewind(t, s, 1)
			if len(offers) != 1 || offers[0] == nil || offers[0].TransactionID != result.TransactionID {
				t.Fatalf("commit offers: %+v", offers)
			}
			switch action {
			case "undo":
				if _, err := s.UndoRewind(result.TransactionID, nil); err != nil {
					t.Fatal(err)
				}
			case "begin":
				s.Begin(2, "next turn", 2)
			case "mutation":
				write(t, path, "external writer")
				s.CaptureAfter(path, CaptureAfterOpts{Seq: 3})
			case "invalidate":
				s.InvalidateUndo()
			}
			s.InvalidateUndo()
			if len(offers) != 2 || offers[1] != nil {
				t.Fatalf("invalidation offers: %+v", offers)
			}
		})
	}
}

func TestFailedRewindDoesNotSignalUndoChange(t *testing.T) {
	s, _, _, _ := twoOwnedTurns(t)
	commitOwnedRewind(t, s, 1)
	changes := 0
	s.OnUndoChange(func() { changes++ })
	plan := prepareOwnedRewind(t, s, 0)
	if _, err := s.CommitRewindWithForward(plan.PlanID, nil, nil, &InjectFail{Phase: "finalize"}); err == nil {
		t.Fatal("expected injected failure")
	}
	if changes != 0 {
		t.Fatalf("failed rewind sent %d changes", changes)
	}
}
