package tutor

import (
	"fmt"
	"strings"
	"time"
)

// IntentArgs carries the per-call inputs intent validation needs. Answers
// non-empty marks a clarify-continuation round; the structured time fields are
// authoritative form input that overrides model extraction.
type IntentArgs struct {
	Answers      []string
	DailyMinutes int // 0 = unspecified
	Deadline     string
	StartDate    string
}

// DegradedIntent is understand_intent's degraded default: generation could
// not run, so the pipeline pauses for clarification instead of guessing.
func DegradedIntent() Intent {
	return Intent{
		NeedsClarify:     true,
		ClarifyQuestions: []string{"无法理解你的学习需求，请补充：你想学什么主题？当前基础如何？每天能投入多少时间？"},
	}
}

// ValidateIntent repairs a model-extracted intent in place, mirroring
// Deep-tutor's _validate_intent: a past deadline clears and asks again, an
// out-of-range daily budget clears, form-specified time fields override
// extraction, and a clarify round that still wants clarification is forced
// forward with neutral defaults rather than looping.
func ValidateIntent(in *Intent, args IntentArgs, today time.Time) {
	if in.Deadline != "" {
		day, err := ParseDate(in.Deadline)
		if err != nil {
			in.Deadline = ""
		} else if day.Before(dateOnly(today)) {
			in.Deadline = ""
			in.NeedsClarify = true
			in.ClarifyQuestions = append(in.ClarifyQuestions,
				fmt.Sprintf("检测到你提供的截止日期（%s）已过，请提供新的截止日期，或说明没有截止时间。", day.Format("2006-01-02")))
		}
	}
	if in.DailyMinutes != 0 && (in.DailyMinutes < 1 || in.DailyMinutes > 1440) {
		in.DailyMinutes = 0
	}
	if in.StartDate != "" {
		if _, err := ParseDate(in.StartDate); err != nil {
			in.StartDate = ""
		}
	}
	if runes := []rune(in.Topic); len(runes) > 200 {
		in.Topic = string(runes[:200])
	}

	if args.DailyMinutes >= 1 && args.DailyMinutes <= 1440 {
		in.DailyMinutes = args.DailyMinutes
	}
	if args.Deadline != "" {
		if _, err := ParseDate(args.Deadline); err == nil {
			in.Deadline = strings.TrimSpace(args.Deadline)
		}
	}
	if args.StartDate != "" {
		if _, err := ParseDate(args.StartDate); err == nil {
			in.StartDate = strings.TrimSpace(args.StartDate)
		}
	}

	if len(args.Answers) > 0 && in.NeedsClarify {
		in.NeedsClarify = false
		in.ClarifyQuestions = nil
		if in.DailyMinutes == 0 {
			in.DailyMinutes = 60
		}
		if in.Level == "" {
			in.Level = "未明确"
		}
		if in.Goal == "" {
			topic := strings.TrimSpace(in.Topic)
			if topic == "" {
				topic = "该主题"
			}
			in.Goal = fmt.Sprintf("系统学习并掌握 %s", topic)
		}
	}
}

// ParseDate parses the wire date shape (ISO YYYY-MM-DD) used across the
// tutor domain.
func ParseDate(s string) (time.Time, error) {
	return time.ParseInLocation("2006-01-02", strings.TrimSpace(s), time.Local)
}

func dateOnly(t time.Time) time.Time {
	y, m, d := t.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, t.Location())
}
