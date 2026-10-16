package control

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"reasonix/internal/base/i18n"
	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/safety/permission"
	"reasonix/internal/state/sessionstore"
)

type askSink struct {
	mu     sync.Mutex
	events []event.Event
}

func (s *askSink) Emit(ev event.Event) {
	s.mu.Lock()
	s.events = append(s.events, ev)
	s.mu.Unlock()
}

func (s *askSink) receipts() []*provider.DecisionReceipt {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []*provider.DecisionReceipt
	for _, ev := range s.events {
		if ev.DecisionReceipt != nil {
			out = append(out, ev.DecisionReceipt)
		}
	}
	return out
}

func askTimeoutController(t *testing.T, seconds int) (*Controller, *askSink) {
	t.Helper()
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	if seconds > 0 {
		path := config.UserConfigPath()
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, fmt.Appendf(nil, "[agent]\nask_timeout_seconds = %d\n", seconds), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	session := sessionstore.NewSession("sys")
	session.Add(provider.Message{Role: provider.RoleAssistant, Content: "asking"})
	sink := &askSink{}
	c := New(Options{Policy: permission.New("ask", nil, nil, nil), Sink: sink, Executor: agent.New(nil, nil, session, agent.Options{}, event.Discard)})
	c.EnableInteractiveApproval()
	return c, sink
}

var askQ = []event.AskQuestion{{ID: "q1", Prompt: "pick one"}}

// An unanswered question under the setting is skipped, says so as a timeout
// the model can tell from an answer, and its card is settled by a receipt.
func TestAskSkipsAfterTheUsersTimeoutAndSaysItWasATimeout(t *testing.T) {
	c, sink := askTimeoutController(t, 10)
	start := time.Now()
	_, err := c.Ask(context.Background(), askQ)
	var timedOut *agent.AskTimedOutError
	if !errors.As(err, &timedOut) || timedOut.After != 10*time.Second {
		t.Fatalf("err = %v, want an AskTimedOutError of 10s", err)
	}
	if time.Since(start) > 20*time.Second {
		t.Fatalf("waited %v for a 10s timeout", time.Since(start))
	}
	if c.approval.hasPending() {
		t.Fatal("the skipped question is still pending")
	}
	got := sink.receipts()
	if len(got) != 1 || got[0].Kind != "ask" || got[0].Outcome != askOutcomeTimedOut {
		t.Fatalf("receipts = %+v, want one ask receipt with outcome timed_out", got)
	}
}

// Off, the default, waits as long as it takes.
func TestAskWithNoTimeoutSettingKeepsWaiting(t *testing.T) {
	c, _ := askTimeoutController(t, 0)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := c.Ask(ctx, askQ)
		done <- err
	}()
	select {
	case err := <-done:
		t.Fatalf("an unset timeout ended the wait: %v", err)
	case <-time.After(1500 * time.Millisecond):
	}
	cancel()
	<-done
}

// A form an external server asked for is not the agent's question to skip:
// its wait carries no ask deadline, the agent's own question does.
func TestExternalFormIsNeverSkippedByTheAskTimeout(t *testing.T) {
	c, _ := askTimeoutController(t, 10)
	_, own, cancelOwn := c.askWaitContext(context.Background(), nil)
	defer cancelOwn()
	if _, ok := own.Deadline(); !ok {
		t.Fatal("the agent's own question carries no ask deadline")
	}
	_, form, cancelForm := c.askWaitContext(context.Background(), &event.AskOrigin{Kind: event.AskOriginMCP})
	defer cancelForm()
	if _, ok := form.Deadline(); ok {
		t.Fatal("an MCP form was given the ask timeout")
	}
}

// An answer that arrives in time is the answer, not a timeout.
func TestAnswerBeforeTheTimeoutWins(t *testing.T) {
	c, sink := askTimeoutController(t, 10)
	type result struct {
		ans []event.AskAnswer
		err error
	}
	done := make(chan result, 1)
	go func() {
		ans, err := c.Ask(context.Background(), askQ)
		done <- result{ans, err}
	}()
	var id string
	for deadline := time.Now().Add(2 * time.Second); id == "" && time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		sink.mu.Lock()
		for _, ev := range sink.events {
			if ev.Kind == event.AskRequest {
				id = ev.Ask.ID
			}
		}
		sink.mu.Unlock()
	}
	c.AnswerQuestion(id, []event.AskAnswer{{QuestionID: "q1", Selected: []string{"A"}}})
	got := <-done
	if got.err != nil || len(got.ans) != 1 || got.ans[0].Selected[0] != "A" {
		t.Fatalf("answer = %+v err = %v, want the user's answer", got.ans, got.err)
	}
	for _, r := range sink.receipts() {
		if r.Outcome == askOutcomeTimedOut {
			t.Fatal("an answered question also recorded a timeout")
		}
	}
}

func TestAskTimeoutSettingRoundTripsAndRefusesWhatCannotWait(t *testing.T) {
	c, _ := askTimeoutController(t, 0)
	if got := c.AskTimeoutSettings(); got.Seconds != 0 || got.DefaultSeconds != config.DefaultAskTimeoutSeconds {
		t.Fatalf("unset reads %+v, want off", got)
	}
	if err := c.SaveAskTimeoutSettings(120); err != nil {
		t.Fatal(err)
	}
	if got := c.AskTimeoutSettings().Seconds; got != 120 {
		t.Fatalf("saved 120, read %d", got)
	}
	if err := c.SaveAskTimeoutSettings(3); !errors.Is(err, config.ErrAskTimeoutOutOfRange) {
		t.Fatalf("3 seconds = %v, want out of range", err)
	}
	if got := c.AskTimeoutSettings().Seconds; got != 120 {
		t.Fatalf("a refused save changed the setting to %d", got)
	}
	if err := c.SaveAskTimeoutSettings(0); err != nil || c.AskTimeoutSettings().Seconds != 0 {
		t.Fatalf("turning it off: %v, now %d", err, c.AskTimeoutSettings().Seconds)
	}
}

type noticeText struct {
	mu    sync.Mutex
	texts []string
}

func (n *noticeText) Emit(ev event.Event) {
	if ev.Kind == event.Notice {
		n.mu.Lock()
		n.texts = append(n.texts, ev.Text)
		n.mu.Unlock()
	}
}

func (n *noticeText) last() string {
	n.mu.Lock()
	defer n.mu.Unlock()
	if len(n.texts) == 0 {
		return ""
	}
	return n.texts[len(n.texts)-1]
}

// The terminal changes the same setting the settings page does.
func TestAskTimeoutSlashCommandReadsAndWritesTheSetting(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	sink := &noticeText{}
	c := New(Options{Sink: sink})

	c.settingsNotice([]string{"/ask-timeout"}, "/ask-timeout")
	if !strings.Contains(sink.last(), i18n.M.AskTimeoutOff) {
		t.Fatalf("bare command = %q, want it to say the timeout is off", sink.last())
	}
	c.settingsNotice([]string{"/ask-timeout", "90"}, "/ask-timeout 90")
	if got := c.AskTimeoutSettings().Seconds; got != 90 || !strings.Contains(sink.last(), "90 s") {
		t.Fatalf("after /ask-timeout 90: seconds=%d notice=%q", got, sink.last())
	}
	c.settingsNotice([]string{"/ask-timeout", "3"}, "/ask-timeout 3")
	if got := c.AskTimeoutSettings().Seconds; got != 90 {
		t.Fatalf("a refused value changed the setting to %d", got)
	}
	c.settingsNotice([]string{"/ask-timeout", "off"}, "/ask-timeout off")
	if got := c.AskTimeoutSettings().Seconds; got != 0 {
		t.Fatalf("/ask-timeout off left %d", got)
	}
	c.settingsNotice([]string{"/ask-timeout", "soon"}, "/ask-timeout soon")
	if got := c.AskTimeoutSettings().Seconds; got != 0 {
		t.Fatalf("an unparseable value changed the setting to %d", got)
	}
	if !strings.Contains(sink.last(), `"soon"`) {
		t.Fatalf("an unparseable argument got %q, want it named back to the user", sink.last())
	}
}

func writeAskTimeoutFile(t *testing.T, body string) {
	t.Helper()
	path := config.UserConfigPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

// A hand-edited value outside the rule is refused with a warning that names
// the problem, and the question waits: nothing is silently off, nothing wraps.
func TestHandEditedAskTimeoutOutsideTheRuleIsRefusedLoudly(t *testing.T) {
	for _, bad := range []string{"-5", "5", "99999999999", "9223372036854775807", "5.5", `"soon"`, "true", `["a"]`} {
		t.Run(bad, func(t *testing.T) {
			c, sink := askTimeoutController(t, 0)
			writeAskTimeoutFile(t, "[agent]\nask_timeout_seconds = "+bad+"\n")
			if got := c.AskTimeoutSettings(); got.Invalid != "ask_timeout.out_of_range" || got.Seconds != 0 {
				t.Fatalf("settings = %+v, want Invalid=ask_timeout.out_of_range and nothing applied", got)
			}
			ctx, cancel := context.WithCancel(context.Background())
			done := make(chan error, 1)
			go func() { _, err := c.Ask(ctx, askQ); done <- err }()
			select {
			case err := <-done:
				t.Fatalf("an invalid timeout ended the wait: %v", err)
			case <-time.After(300 * time.Millisecond):
			}
			cancel()
			<-done
			var warned bool
			sink.mu.Lock()
			for _, ev := range sink.events {
				warned = warned || (ev.Kind == event.Notice && ev.Code == event.NoticeCodeAskTimeoutInvalid && ev.Level == event.LevelWarn)
			}
			sink.mu.Unlock()
			if !warned {
				t.Fatal("no ask_timeout_invalid warning reached the frontends")
			}
		})
	}
}

func TestAskTimeoutRuleIsOneFunction(t *testing.T) {
	for n, ok := range map[int]bool{0: true, 10: true, 86400: true, 9: false, 86401: false, -1: false} {
		if got := config.ValidateAskTimeoutSeconds(n) == nil; got != ok {
			t.Errorf("ValidateAskTimeoutSeconds(%d) ok=%v, want %v", n, got, ok)
		}
	}
}

// Cancelling the turn is a cancellation, never a timeout, even with the
// setting on.
func TestCancelledAskIsNotATimeout(t *testing.T) {
	c, sink := askTimeoutController(t, 30)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { _, err := c.Ask(ctx, askQ); done <- err }()
	time.Sleep(100 * time.Millisecond)
	cancel()
	err := <-done
	if _, timedOut := errors.AsType[*agent.AskTimedOutError](err); timedOut || !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want a plain cancellation", err)
	}
	for _, r := range sink.receipts() {
		if r.Outcome == askOutcomeTimedOut {
			t.Fatal("a cancelled ask recorded a timeout")
		}
	}
}

// The headless approval deadline, when shorter, ends the wait as itself.
func TestApprovalDeadlineBeforeTheAskTimeoutKeepsItsOwnIdentity(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	writeAskTimeoutFile(t, "[agent]\nask_timeout_seconds = 3600\n")
	c := New(Options{Policy: permission.New("ask", nil, nil, nil), Sink: &askSink{}, ApprovalTimeout: 40 * time.Millisecond})
	c.EnableInteractiveApproval()
	_, err := c.Ask(context.Background(), askQ)
	if _, timedOut := errors.AsType[*agent.AskTimedOutError](err); timedOut || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err = %v, want the approval deadline, not an ask timeout", err)
	}
}

// An answer racing the timer is one or the other, exactly once: never both,
// never a lost answer, and the journal agrees with what was returned. The race
// is the moment the wait ends against AnswerQuestion, driven directly because
// the timeout's own floor is ten seconds.
func TestAnswerRacingTheTimerResolvesExactlyOnce(t *testing.T) {
	for i := range 50 {
		c, sink := askTimeoutController(t, 0)
		id := c.approval.nextAskID()
		reply := c.approval.registerAsk(id, askQ, nil)
		waitCtx, cancel := context.WithCancelCause(context.Background())
		cancel(&agent.AskTimedOutError{After: 10 * time.Second})

		type result struct {
			ans []event.AskAnswer
			err error
		}
		done := make(chan result, 1)
		start := make(chan struct{})
		go func() {
			<-start
			a, err := c.endUnansweredAsk(waitCtx, id, reply)
			done <- result{a, err}
		}()
		go func() {
			<-start
			c.AnswerQuestion(id, []event.AskAnswer{{QuestionID: "q1", Selected: []string{"A"}}})
		}()
		close(start)
		got := <-done
		_, timedOut := errors.AsType[*agent.AskTimedOutError](got.err)
		answered := got.err == nil && len(got.ans) == 1
		if timedOut == answered {
			t.Fatalf("iteration %d: timedOut=%v answered=%v err=%v", i, timedOut, answered, got.err)
		}
		var receiptsTimed int
		for _, r := range sink.receipts() {
			if r.Outcome == askOutcomeTimedOut {
				receiptsTimed++
			}
		}
		if (receiptsTimed == 1) != timedOut {
			t.Fatalf("iteration %d: %d timed_out receipts for timedOut=%v", i, receiptsTimed, timedOut)
		}
	}
}
