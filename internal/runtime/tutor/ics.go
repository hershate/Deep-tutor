package tutor

import (
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// ics.go renders a plan's schedule as RFC 5545: one all-day event per study
// day, description listing the day's tasks and estimated minutes, in
// Deep-tutor's shape.

// ErrNoSchedule marks a plan whose schedule holds no usable day.
var ErrNoSchedule = errors.New("tutor: plan has no usable schedule")

// icsEscape applies RFC 5545 TEXT escaping: backslash, semicolon, comma and
// line breaks.
func icsEscape(text string) string {
	r := strings.NewReplacer(
		`\`, `\\`,
		`;`, `\;`,
		`,`, `\,`,
		"\r\n", `\n`,
		"\n", `\n`,
		"\r", `\n`,
	)
	return r.Replace(text)
}

// icsFold splits a content line at 75 UTF-8 bytes with space-led
// continuations (RFC 5545 §3.1).
func icsFold(line string) []string {
	if len(line) <= 75 {
		return []string{line}
	}
	var parts []string
	var buf []byte
	for i := 0; i < len(line); {
		_, size := utf8.DecodeRuneInString(line[i:])
		if size == 0 {
			size = 1
		}
		room := 75
		if len(parts) > 0 {
			room = 74 // continuation lines spend one byte on the space
		}
		if len(buf)+size > room {
			parts = append(parts, string(buf))
			buf = append([]byte{' '}, line[i:i+size]...)
			i += size
			continue
		}
		buf = append(buf, line[i:i+size]...)
		i += size
	}
	if len(buf) > 0 {
		parts = append(parts, string(buf))
	}
	return parts
}

// BuildICS renders the calendar text. Malformed dates are skipped and
// counted; a schedule with no valid day is ErrNoSchedule.
func BuildICS(plan *Plan, now time.Time) (text string, events, skipped int, err error) {
	stageNames := map[int]string{}
	for _, st := range plan.Stages {
		stageNames[st.Index] = st.Name
	}
	title := plan.Title
	if title == "" {
		title = plan.Topic
	}
	if title == "" {
		title = "学习计划"
	}
	dtstamp := now.UTC().Format("20060102T150405Z")
	lines := []string{
		"BEGIN:VCALENDAR",
		"VERSION:2.0",
		"PRODID:-//Reasonix//Tutor Study Schedule//CN",
		"CALSCALE:GREGORIAN",
		"X-WR-CALNAME:" + icsEscape("Reasonix Tutor："+title),
	}
	for i, day := range plan.Schedule {
		parsed, perr := time.ParseInLocation("2006-01-02", strings.TrimSpace(day.Date), time.Local)
		if perr != nil {
			skipped++
			continue
		}
		stageName := stageNames[day.StageIndex]
		summary := fmt.Sprintf("[%s] ", title)
		if stageName != "" {
			summary += fmt.Sprintf("阶段%d %s", day.StageIndex+1, stageName)
		} else {
			summary += fmt.Sprintf("学习日 %d", i+1)
		}
		var desc []string
		if stageName != "" {
			desc = append(desc, "阶段："+stageName)
		}
		for _, task := range day.Tasks {
			desc = append(desc, "· "+task)
		}
		if day.EstMinutes > 0 {
			desc = append(desc, fmt.Sprintf("预计时长：%d 分钟", day.EstMinutes))
		}
		if len(desc) == 0 {
			desc = []string{"学习日"}
		}
		event := []string{
			"BEGIN:VEVENT",
			fmt.Sprintf("UID:dt-%s-%d-%s@reasonix", plan.ID, i, parsed.Format("2006-01-02")),
			"DTSTAMP:" + dtstamp,
			"DTSTART;VALUE=DATE:" + parsed.Format("20060102"),
			"SUMMARY:" + icsEscape(summary),
			"DESCRIPTION:" + icsEscape(strings.Join(desc, "\n")),
			"END:VEVENT",
		}
		for _, ln := range event {
			lines = append(lines, icsFold(ln)...)
		}
		events++
	}
	if events == 0 {
		return "", 0, skipped, ErrNoSchedule
	}
	lines = append(lines, "END:VCALENDAR")
	return strings.Join(lines, "\r\n") + "\r\n", events, skipped, nil
}
