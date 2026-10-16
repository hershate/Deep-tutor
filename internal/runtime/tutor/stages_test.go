package tutor

import (
	"errors"
	"testing"
)

func TestValidateStagesCount(t *testing.T) {
	for n, ok := range map[int]bool{2: false, 3: true, 4: true, 6: true, 7: false} {
		stages := make([]Stage, n)
		err := ValidateStages(stages)
		if ok && err != nil {
			t.Fatalf("%d stages must pass, got %v", n, err)
		}
		if !ok && !errors.Is(err, ErrStageCount) {
			t.Fatalf("%d stages must fail with ErrStageCount, got %v", n, err)
		}
	}
}

func TestNormalizeStagesClampsDifficulty(t *testing.T) {
	stages := []Stage{{Difficulty: 0}, {Difficulty: 3}, {Difficulty: 99}}
	NormalizeStages(stages)
	want := []int{1, 3, 5}
	for i, w := range want {
		if stages[i].Difficulty != w {
			t.Fatalf("stage %d difficulty: got %d want %d", i, stages[i].Difficulty, w)
		}
	}
}

func TestDefaultStagesShape(t *testing.T) {
	stages := DefaultStages()
	if len(stages) != 3 {
		t.Fatalf("default ladder has 3 stages: %d", len(stages))
	}
	if err := ValidateStages(stages); err != nil {
		t.Fatalf("default stages satisfy the contract: %v", err)
	}
}
