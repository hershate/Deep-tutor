package tutor

import (
	"path/filepath"
	"sync"
	"time"
)

// usage.go is the tutor's cost ledger: one JSON file per local day, summed
// before every LLM-driven run. Deep-tutor keeps this in its api_usage table;
// the file store holds the same three numbers.

// usageMu serializes read-modify-write cycles across concurrent runs.
var usageMu sync.Mutex

// DailyUsage is one day of tutor spend.
type DailyUsage struct {
	Date string  `json:"date"`
	Cost float64 `json:"cost"`
	Runs int     `json:"runs"`
}

func (s *Store) usagePath(date string) string {
	return filepath.Join(s.root, "usage", date+".json")
}

// UsageOfDay returns the day's recorded spend; an absent file is a zero day.
func (s *Store) UsageOfDay(date string) (DailyUsage, error) {
	var usage DailyUsage
	if err := s.readJSON(s.usagePath(date), &usage); err != nil {
		return DailyUsage{Date: date}, nil
	}
	usage.Date = date
	return usage, nil
}

// AddUsage adds one run's cost to the day's ledger.
func (s *Store) AddUsage(date string, cost float64) error {
	usageMu.Lock()
	defer usageMu.Unlock()
	usage, err := s.UsageOfDay(date)
	if err != nil {
		usage = DailyUsage{Date: date}
	}
	usage.Cost += cost
	usage.Runs++
	return s.writeJSON(s.usagePath(date), usage)
}

// UsageSummary is the tutor usage view: today against the daily budget and
// the trailing day-by-day series, honestly limited to what the ledger keeps
// (cost and runs per day — no per-model split exists in the file store).
type UsageSummary struct {
	Today         DailyUsage   `json:"today"`
	DailyBudget   float64      `json:"daily_budget"`
	BudgetEnabled bool         `json:"budget_enabled"`
	Daily         []DailyUsage `json:"daily"`
	TotalCost     float64      `json:"total_cost"`
	TotalRuns     int          `json:"total_runs"`
}

// UsageSeries returns the last `days` days ending at now, zero-filled where
// no ledger file exists.
func (s *Store) UsageSeries(days int, now time.Time) ([]DailyUsage, error) {
	if days < 1 {
		days = 1
	}
	if days > 90 {
		days = 90
	}
	out := make([]DailyUsage, 0, days)
	day := now
	for range days {
		date := day.Format("2006-01-02")
		usage, err := s.UsageOfDay(date)
		if err != nil {
			usage = DailyUsage{Date: date}
		}
		out = append(out, usage)
		day = day.AddDate(0, 0, -1)
	}
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out, nil
}

// UsageOverview assembles the usage view over the ledger. budget ≤ 0 means
// no daily cap is enforced.
func (s *Store) UsageOverview(days int, budget float64, now time.Time) (UsageSummary, error) {
	if days < 1 {
		days = 14
	}
	series, err := s.UsageSeries(days, now)
	if err != nil {
		return UsageSummary{}, err
	}
	today, err := s.UsageOfDay(now.Format("2006-01-02"))
	if err != nil {
		today = DailyUsage{Date: now.Format("2006-01-02")}
	}
	summary := UsageSummary{
		Today: today, DailyBudget: budget, BudgetEnabled: budget > 0, Daily: series,
	}
	for _, d := range series {
		summary.TotalCost += d.Cost
		summary.TotalRuns += d.Runs
	}
	return summary, nil
}
