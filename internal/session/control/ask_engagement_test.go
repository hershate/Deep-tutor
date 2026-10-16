package control

import (
	"context"
	"errors"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/runtime/agent"
)

func TestEngagementAndExpiryChooseOneOutcome(t *testing.T) {
	for range 50 {
		c, sink := askTimeoutController(t, 0)
		id := c.approval.nextAskID()
		reply := c.approval.registerAsk(id, askQ, nil)
		c.approval.markAskEmitted(id)
		ctx, cancel := context.WithCancelCause(context.Background())
		cancel(&agent.AskTimedOutError{After: 10 * time.Second})
		start := make(chan struct{})
		engaged := make(chan bool, 1)
		ended := make(chan error, 1)
		go func() { <-start; engaged <- c.EngageQuestion(id) }()
		go func() { <-start; _, err := c.endUnansweredAsk(ctx, id, reply); ended <- err }()
		close(start)
		accepted, err := <-engaged, <-ended
		if accepted {
			if !errors.Is(err, errAskEngaged) || !c.approval.hasPending() || len(sink.receipts()) != 0 {
				t.Fatalf("accepted engagement lost to expiry: %v, %+v", err, sink.receipts())
			}
			c.AnswerQuestion(id, []event.AskAnswer{{QuestionID: "q1", Selected: []string{"A"}}})
		} else if _, ok := errors.AsType[*agent.AskTimedOutError](err); !ok || c.approval.hasPending() || len(sink.receipts()) != 1 {
			t.Fatalf("expiry was not settled exactly once: %v, %+v", err, sink.receipts())
		}
		c.Close()
	}
}

func TestEngagementDoesNotTouchAnotherPromptOrTheGlobalSetting(t *testing.T) {
	c, _ := askTimeoutController(t, 10)
	t.Cleanup(c.Close)
	queued := c.approval.nextAskID()
	c.approval.registerAsk(queued, askQ, nil)
	if c.EngageQuestion(queued) || c.EngageQuestion("missing") {
		t.Fatal("unseen or missing question accepted engagement")
	}
	c.approval.cancelAsk(queued)
	external := c.approval.nextAskID()
	c.approval.registerAsk(external, askQ, &event.AskOrigin{Kind: event.AskOriginMCP})
	c.approval.markAskEmitted(external)
	if c.EngageQuestion(external) {
		t.Fatal("engagement changed an external form")
	}
	c.approval.cancelAsk(external)
	id := c.approval.nextAskID()
	c.approval.registerAsk(id, askQ, nil)
	c.approval.markAskEmitted(id)
	for range 2 {
		if !c.EngageQuestion(id) {
			t.Fatal("engagement is not idempotent")
		}
	}
	if c.AskTimeoutSettings().Seconds != 10 {
		t.Fatal("engagement changed the global timeout")
	}
	c.approval.cancelAsk(id)
	if c.EngageQuestion(id) {
		t.Fatal("stale engagement accepted")
	}
}

func TestEngagementKeepsTheParentDeadline(t *testing.T) {
	c, sink := askTimeoutController(t, 10)
	t.Cleanup(c.Close)
	c.approval.approvalTimeout = 100 * time.Millisecond
	asked := make(chan string, 1)
	c.sink = event.FuncSink(func(e event.Event) {
		sink.Emit(e)
		if e.Kind == event.AskRequest {
			asked <- e.Ask.ID
		}
	})
	done := make(chan error, 1)
	go func() { _, err := c.Ask(context.Background(), askQ); done <- err }()
	if !c.EngageQuestion(<-asked) {
		t.Fatal("could not engage the emitted question")
	}
	select {
	case err := <-done:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("parent deadline became %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("engagement removed the parent deadline")
	}
	if len(sink.receipts()) != 0 {
		t.Fatal("parent cancellation was recorded as an answer or skip")
	}
}
