package tutor

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func icsPlan() *Plan {
	return &Plan{
		ID:    "p1",
		Title: "Go 入门",
		Stages: []Stage{
			{Index: 0, Name: "基础"},
			{Index: 1, Name: "进阶"},
		},
		Schedule: []ScheduleDay{
			{Date: "2026-10-15", StageIndex: 0, Tasks: []string{"读第一章", "跑示例"}, EstMinutes: 45},
			{Date: "bad-date", StageIndex: 1},
			{Date: "2026-10-16", StageIndex: 1, Tasks: []string{"练习题"}},
		},
	}
}

// unfold reverses RFC 5545 folding so assertions see logical lines.
func unfold(text string) string {
	return strings.ReplaceAll(text, "\r\n ", "")
}

func TestBuildICS(t *testing.T) {
	text, events, skipped, err := BuildICS(icsPlan(), time.Date(2026, 10, 14, 12, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("BuildICS: %v", err)
	}
	if events != 2 || skipped != 1 {
		t.Fatalf("events=%d skipped=%d", events, skipped)
	}
	flat := unfold(text)
	for _, want := range []string{
		"BEGIN:VCALENDAR",
		"PRODID:-//Reasonix//Tutor Study Schedule//CN",
		"DTSTART;VALUE=DATE:20261015",
		"SUMMARY:[Go 入门] 阶段1 基础",
		"DESCRIPTION:阶段：基础\\n· 读第一章\\n· 跑示例\\n预计时长：45 分钟",
		"UID:dt-p1-0-2026-10-15@reasonix",
		"END:VCALENDAR",
	} {
		if !strings.Contains(flat, want) {
			t.Fatalf("missing %q in:\n%s", want, flat)
		}
	}
	if !strings.HasSuffix(text, "\r\n") {
		t.Fatal("CRLF line endings required")
	}
}

func TestBuildICSFoldsLongLines(t *testing.T) {
	plan := icsPlan()
	plan.Schedule = []ScheduleDay{{Date: "2026-10-15", StageIndex: 0, Tasks: []string{strings.Repeat("很长的任务说明", 40)}, EstMinutes: 999}}
	text, _, _, err := BuildICS(plan, time.Now())
	if err != nil {
		t.Fatalf("BuildICS: %v", err)
	}
	for line := range strings.SplitSeq(strings.TrimSuffix(text, "\r\n"), "\r\n") {
		if len(line) > 75 {
			t.Fatalf("unfolded line of %d bytes: %q", len(line), line)
		}
	}
	// Continuation lines must start with a space and reassembly must contain
	// the full task text.
	if !strings.Contains(text, "\r\n ") {
		t.Fatal("no continuation lines")
	}
	if !strings.Contains(unfold(text), strings.Repeat("很长的任务说明", 40)) {
		t.Fatal("folded content lost text")
	}
}

func TestBuildICSNoSchedule(t *testing.T) {
	plan := icsPlan()
	plan.Schedule = nil
	if _, _, _, err := BuildICS(plan, time.Now()); !errors.Is(err, ErrNoSchedule) {
		t.Fatalf("err = %v, want ErrNoSchedule", err)
	}
	plan.Schedule = []ScheduleDay{{Date: "nope"}}
	if _, _, _, err := BuildICS(plan, time.Now()); !errors.Is(err, ErrNoSchedule) {
		t.Fatalf("err = %v, want ErrNoSchedule", err)
	}
}
