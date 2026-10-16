package tutor

import (
	"fmt"
	"time"
)

// MaxScheduleDays caps a generated schedule.
const MaxScheduleDays = 365

// CheckSchedule is schedule_plan's deterministic post-processing: minutes
// clamp non-negative, over-long schedules truncate, dates are rewritten
// consecutively from startDate (or today) so the calendar always matches what
// the user asked for, and total load is checked against span capacity. An
// insufficient schedule warns; it never blocks delivery.
func CheckSchedule(data *ScheduleData, dailyMinutes int, startDate string, today time.Time) {
	for i := range data.Schedule {
		if data.Schedule[i].EstMinutes < 0 {
			data.Schedule[i].EstMinutes = 0
		}
	}
	if len(data.Schedule) > MaxScheduleDays {
		data.Schedule = data.Schedule[:MaxScheduleDays]
		data.Truncated = true
	}
	start := dateOnly(today)
	if d, err := ParseDate(startDate); err == nil {
		start = d
	}
	for i := range data.Schedule {
		data.Schedule[i].Date = start.AddDate(0, 0, i).Format("2006-01-02")
	}
	data.StartDate = start.Format("2006-01-02")

	total := 0
	for _, day := range data.Schedule {
		total += day.EstMinutes
	}
	data.TotalMinutes = total
	if dailyMinutes <= 0 || len(data.Schedule) == 0 {
		return
	}
	data.RequiredDays = (total + dailyMinutes - 1) / dailyMinutes

	earliest, latest := data.Schedule[0].Date, data.Schedule[0].Date
	for _, day := range data.Schedule[1:] {
		if day.Date < earliest {
			earliest = day.Date
		}
		if day.Date > latest {
			latest = day.Date
		}
	}
	first, errFirst := ParseDate(earliest)
	last, errLast := ParseDate(latest)
	if errFirst != nil || errLast != nil {
		return
	}
	span := int(last.Sub(first).Hours()/24) + 1
	data.SpanDays = span
	data.CapacityMinutes = span * dailyMinutes
	if total > data.CapacityMinutes {
		data.Warning = "insufficient_time"
		data.Suggestion = fmt.Sprintf("总时长 %d 分钟超过排期 %d 天的容量 %d 分钟；建议压缩任务/延期/降级",
			total, span, data.CapacityMinutes)
	}
}
