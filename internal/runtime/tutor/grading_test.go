package tutor

import "testing"

func TestGradeChoice(t *testing.T) {
	correct := GradeChoice("React Hooks", "React Hooks")
	if correct == nil || !*correct {
		t.Fatalf("matching selection must grade correct")
	}
	wrong := GradeChoice("React Hooks", "class components")
	if wrong == nil || *wrong {
		t.Fatalf("non-matching selection must grade incorrect")
	}
	if unknown := GradeChoice("", "anything"); unknown != nil {
		t.Fatalf("missing answer grades unknown, got %v", *unknown)
	}
}

func TestNormalizeQuizType(t *testing.T) {
	cases := map[string]QuizType{
		"choice": QuizChoice,
		"fill":   QuizFill,
		"short":  QuizShort,
		"essay":  QuizEssay,
		"":       QuizChoice,
		"weird":  QuizChoice,
	}
	for in, want := range cases {
		if got := NormalizeQuizType(in); got != want {
			t.Fatalf("NormalizeQuizType(%q) = %v, want %v", in, got, want)
		}
	}
}
