package tutor

import (
	"testing"
	"time"
)

func TestComputeMastery(t *testing.T) {
	perfect := 1.0
	half := 0.5
	cases := []struct {
		name   string
		old    float64
		status ProgressStatus
		score  *float64
		want   float64
	}{
		{"perfect quiz reaches 1.0", 0.2, ProgressLearning, &perfect, 1.0},
		{"blend rounds to 3 decimals", 0.0, ProgressLearning, &half, 0.5},
		{"blend floors at the score", 0.9, ProgressLearning, &half, 0.62},
		{"done sets full mastery", 0.4, ProgressDone, nil, 1.0},
		{"done never lowers", 1.0, ProgressDone, nil, 1.0},
		{"learning leaves unchanged", 0.33, ProgressLearning, nil, 0.33},
		{"not_started leaves unchanged", 0.5, ProgressNotStarted, nil, 0.5},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := ComputeMastery(tc.old, tc.status, tc.score)
			if got != tc.want {
				t.Fatalf("ComputeMastery(%v, %v, %v) = %v, want %v", tc.old, tc.status, tc.score, got, tc.want)
			}
		})
	}
}

func TestComputeMasteryClampsInputs(t *testing.T) {
	over := 1.5
	if got := ComputeMastery(-2, ProgressLearning, &over); got != 1.0 {
		t.Fatalf("inputs clamp to [0,1]: got %v", got)
	}
}

func TestNextReviewDate(t *testing.T) {
	today := time.Date(2026, 10, 14, 15, 0, 0, 0, time.Local)
	cases := []struct {
		mastery float64
		days    int
	}{
		{0.0, 1}, {0.49, 1},
		{0.5, 3}, {0.69, 3},
		{0.7, 7}, {0.84, 7},
		{0.85, 14}, {1.0, 14},
	}
	for _, tc := range cases {
		want := today.AddDate(0, 0, tc.days)
		if got := NextReviewDate(tc.mastery, today); !got.Equal(want) {
			t.Fatalf("NextReviewDate(%v) = %v, want %v", tc.mastery, got, want)
		}
	}
}
