// tutor_export.go - the tutor plan exports over HTTP: markdown, the
// RFC 5545 calendar, the Anki card package, and the self-contained HTML
// slide decks for whole plans and single self-study units.
package serve

import (
	"net/http"
	"strconv"
	"time"

	"reasonix/internal/runtime/tutor"
)

func (s *Server) tutorPlanExport(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	plan, err := svc.Store().Plan(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="plan-`+plan.ID+`.md"`)
	_, _ = w.Write([]byte(tutor.RenderPlanMarkdown(plan)))
}

func (s *Server) tutorPlanExportICS(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	plan, err := svc.Store().Plan(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	text, events, skipped, err := tutor.BuildICS(plan, time.Now())
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	w.Header().Set("Content-Type", "text/calendar; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="plan-`+plan.ID+`.ics"`)
	if skipped > 0 {
		w.Header().Set("X-Tutor-Ics-Skipped", strconv.Itoa(skipped))
	}
	w.Header().Set("X-Tutor-Ics-Events", strconv.Itoa(events))
	_, _ = w.Write([]byte(text))
}

// tutorPlanExportPDF is Deep-tutor's degraded pdf path made the whole
// feature: Reasonix deliberately ships no print renderer (weasyprint's
// role), so the pdf request serves the markdown with the degradation
// warning, exactly what Deep-tutor answers without weasyprint installed.
func (s *Server) tutorPlanExportPDF(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	plan, err := svc.Store().Plan(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="plan-`+plan.ID+`.md"`)
	w.Header().Set("X-Tutor-Export-Warning", "pdf+degraded%3A+Markdown")
	_, _ = w.Write([]byte(tutor.RenderPlanMarkdown(plan)))
}

// tutorPlanExportApkg streams the plan's quizzes as an Anki .apkg deck.
// ?wrong_only=1 selects the wrong-book variant — quizzes whose latest
// attempt graded false, exported under a sibling deck.
func (s *Server) tutorPlanExportApkg(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	plan, err := svc.Store().Plan(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	quizzes, err := svc.Store().Quizzes(plan.ID)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	attempts, err := svc.Store().Attempts(plan.ID)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	wrongOnly := r.URL.Query().Get("wrong_only") == "1" || r.URL.Query().Get("wrong_only") == "true"
	blob, cards, err := tutor.BuildApkg(plan, quizzes, attempts, wrongOnly, time.Now())
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	name := "plan-" + plan.ID
	if wrongOnly {
		name += "-wrong"
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="`+name+`.apkg"`)
	w.Header().Set("X-Tutor-Cards", strconv.Itoa(cards))
	_, _ = w.Write(blob)
}

// tutorPlanExportSlides renders the whole plan as a self-contained HTML
// slide deck (LLM spec, deterministic fallback deck when no model answers).
func (s *Server) tutorPlanExportSlides(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	res, err := svc.GeneratePlanSlides(r.Context(), r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="plan-`+r.PathValue("pid")+`.slides.html"`)
	w.Header().Set("X-Tutor-Slides", strconv.Itoa(res.SlideCount))
	if res.Degraded {
		w.Header().Set("X-Tutor-Slides-Degraded", "1")
	}
	_, _ = w.Write([]byte(res.HTML))
}

// tutorUnitExportSlides renders one self-study unit the same way.
func (s *Server) tutorUnitExportSlides(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	res, err := svc.GenerateUnitSlides(r.Context(), r.PathValue("pid"), r.PathValue("uid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="unit-`+r.PathValue("uid")+`.slides.html"`)
	w.Header().Set("X-Tutor-Slides", strconv.Itoa(res.SlideCount))
	if res.Degraded {
		w.Header().Set("X-Tutor-Slides-Degraded", "1")
	}
	_, _ = w.Write([]byte(res.HTML))
}
