package tutor

import (
	"testing"
	"time"
)

var today = time.Date(2026, 10, 14, 9, 0, 0, 0, time.Local)

func TestValidateIntentPastDeadlineAsksAgain(t *testing.T) {
	in := &Intent{Topic: "Go", Deadline: "2020-01-01", NeedsClarify: false}
	ValidateIntent(in, IntentArgs{}, today)
	if in.Deadline != "" {
		t.Fatalf("past deadline clears: %q", in.Deadline)
	}
	if !in.NeedsClarify || len(in.ClarifyQuestions) != 1 {
		t.Fatalf("past deadline converts to a clarify question: %+v", in)
	}
}

func TestValidateIntentInvalidFieldsClear(t *testing.T) {
	in := &Intent{
		Topic:        "Go",
		Deadline:     "not-a-date",
		DailyMinutes: 5000,
		StartDate:    "bad",
	}
	ValidateIntent(in, IntentArgs{}, today)
	if in.Deadline != "" || in.DailyMinutes != 0 || in.StartDate != "" {
		t.Fatalf("invalid deadline/daily/start clear: %+v", in)
	}
}

func TestValidateIntentTruncatesTopic(t *testing.T) {
	long := make([]rune, 250)
	for i := range long {
		long[i] = '学'
	}
	in := &Intent{Topic: string(long)}
	ValidateIntent(in, IntentArgs{}, today)
	if got := len([]rune(in.Topic)); got != 200 {
		t.Fatalf("topic truncates at 200 runes: got %d", got)
	}
}

func TestValidateIntentFormOverridesWin(t *testing.T) {
	in := &Intent{Topic: "Go", DailyMinutes: 30, Deadline: "2030-01-01"}
	ValidateIntent(in, IntentArgs{DailyMinutes: 90, Deadline: "2031-06-30", StartDate: "2026-11-01"}, today)
	if in.DailyMinutes != 90 || in.Deadline != "2031-06-30" || in.StartDate != "2026-11-01" {
		t.Fatalf("structured form fields are authoritative: %+v", in)
	}
}

func TestValidateIntentFormOverrideInvalidIgnored(t *testing.T) {
	in := &Intent{Topic: "Go", DailyMinutes: 30}
	ValidateIntent(in, IntentArgs{DailyMinutes: 9999, Deadline: "garbage"}, today)
	if in.DailyMinutes != 30 || in.Deadline != "" {
		t.Fatalf("invalid overrides must not clobber extraction: %+v", in)
	}
}

func TestValidateIntentClarifyRoundForcesForward(t *testing.T) {
	in := &Intent{Topic: "React", NeedsClarify: true, ClarifyQuestions: []string{"基础如何？"}}
	ValidateIntent(in, IntentArgs{Answers: []string{"零基础"}}, today)
	if in.NeedsClarify || len(in.ClarifyQuestions) != 0 {
		t.Fatalf("answered clarify never asks again: %+v", in)
	}
	if in.DailyMinutes != 60 {
		t.Fatalf("neutral default is one hour: %d", in.DailyMinutes)
	}
	if in.Level != "未明确" || in.Goal != "系统学习并掌握 React" {
		t.Fatalf("neutral defaults fill missing dimensions: %+v", in)
	}
}

func TestValidateIntentNoAnswersKeepsClarify(t *testing.T) {
	in := &Intent{Topic: "React", NeedsClarify: true, ClarifyQuestions: []string{"基础如何？"}}
	ValidateIntent(in, IntentArgs{}, today)
	if !in.NeedsClarify || len(in.ClarifyQuestions) != 1 {
		t.Fatalf("first round clarify survives: %+v", in)
	}
}
