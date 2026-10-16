package tutor

import (
	"testing"
)

func TestCheckScheduleRewritesDatesFromStart(t *testing.T) {
	data := &ScheduleData{Schedule: []ScheduleDay{
		{Date: "1999-01-01", StageIndex: 0, Tasks: []string{"a"}, EstMinutes: 60},
		{Date: "1999-01-02", StageIndex: 0, Tasks: []string{"b"}, EstMinutes: 30},
	}}
	CheckSchedule(data, 60, "2026-11-01", today)
	if data.Schedule[0].Date != "2026-11-01" || data.Schedule[1].Date != "2026-11-02" {
		t.Fatalf("dates rewrite consecutively from start_date: %+v", data.Schedule)
	}
	if data.StartDate != "2026-11-01" || data.TotalMinutes != 90 || data.RequiredDays != 2 {
		t.Fatalf("derived totals: start=%s total=%d required=%d", data.StartDate, data.TotalMinutes, data.RequiredDays)
	}
	if data.Warning != "" {
		t.Fatalf("fits capacity without warning: %q", data.Warning)
	}
}

func TestCheckScheduleFallsBackToToday(t *testing.T) {
	data := &ScheduleData{Schedule: []ScheduleDay{{Date: "x", EstMinutes: 30}}}
	CheckSchedule(data, 0, "", today)
	if data.Schedule[0].Date != "2026-10-14" {
		t.Fatalf("no start_date falls back to today: %q", data.Schedule[0].Date)
	}
	if data.RequiredDays != 0 {
		t.Fatalf("no daily budget skips capacity math: %d", data.RequiredDays)
	}
}

func TestCheckScheduleWarnsOnInsufficientTime(t *testing.T) {
	data := &ScheduleData{Schedule: []ScheduleDay{
		{Date: "2026-11-01", EstMinutes: 600},
	}}
	CheckSchedule(data, 60, "2026-11-01", today)
	if data.Warning != "insufficient_time" || data.CapacityMinutes != 60 || data.SpanDays != 1 {
		t.Fatalf("single overloaded day still warns: %+v", data)
	}
	if data.Suggestion == "" {
		t.Fatalf("warning carries a suggestion")
	}
}

func TestCheckScheduleClampsAndTruncates(t *testing.T) {
	days := make([]ScheduleDay, MaxScheduleDays+10)
	for i := range days {
		days[i] = ScheduleDay{EstMinutes: -5}
	}
	data := &ScheduleData{Schedule: days}
	CheckSchedule(data, 0, "2026-11-01", today)
	if !data.Truncated || len(data.Schedule) != MaxScheduleDays {
		t.Fatalf("over-long schedule truncates: len=%d truncated=%v", len(data.Schedule), data.Truncated)
	}
	if data.TotalMinutes != 0 {
		t.Fatalf("negative minutes clamp to zero: %d", data.TotalMinutes)
	}
}
