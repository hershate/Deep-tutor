package serve

import (
	"html/template"
	"net/http"

	"reasonix/internal/runtime/tutor"
)

// The plan-share surface: an owner track (create/revoke/status/list) and a
// public read-only track over the share token itself. The token is 192-bit
// random, so the public page's guard is unguessability — the same property
// Deep Tutor's public /shared/{token} relied on; the server's host binding
// still applies.
func (s *Server) registerTutorShareRoutes(mux *http.ServeMux) {
	mux.HandleFunc("POST /tutor/plans/{id}/share", s.tutorShareCreate)
	mux.HandleFunc("GET /tutor/plans/{id}/share", s.tutorShareStatus)
	mux.HandleFunc("DELETE /tutor/plans/{id}/share", s.tutorShareRevoke)
	mux.HandleFunc("GET /tutor/shares", s.tutorShares)
	mux.HandleFunc("GET /tutor/shared/{token}", s.tutorSharedPlan)
}

func (s *Server) tutorShareCreate(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	sh, err := svc.SharePlan(r.PathValue("id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusCreated)
	writeJSON(w, shareResponse(sh))
}

func (s *Server) tutorShareStatus(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	sh, err := svc.ShareStatus(r.PathValue("id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	if sh == nil {
		writeJSON(w, map[string]any{"shared": false})
		return
	}
	writeJSON(w, shareResponse(sh))
}

func (s *Server) tutorShareRevoke(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if err := svc.ShareRevoke(r.PathValue("id")); err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) tutorShares(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	entries, err := svc.ShareList()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	out := make([]map[string]any, 0, len(entries))
	for i := range entries {
		out = append(out, shareListRow(&entries[i]))
	}
	writeJSON(w, map[string]any{"shares": out})
}

func (s *Server) tutorSharedPlan(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	view, err := svc.SharedPlanView(r.PathValue("token"))
	if err != nil {
		shareNotFoundPage(w)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	if r.URL.Query().Get("format") == "json" {
		writeJSON(w, view)
		return
	}
	renderSharedPlan(w, view)
}

func shareResponse(sh *tutor.Share) map[string]any {
	return map[string]any{
		"shared":     true,
		"token":      sh.Token,
		"path":       "/tutor/shared/" + sh.Token,
		"created_at": sh.CreatedAt,
	}
}

// shareListRow is the management row: the share response plus the plan it
// points at, so the Studio list can revoke by plan.
func shareListRow(e *tutor.ShareEntry) map[string]any {
	row := shareResponse(&e.Share)
	row["plan_id"] = e.PlanID
	row["title"] = e.Title
	row["topic"] = e.Topic
	return row
}

func shareNotFoundPage(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusNotFound)
	_, _ = w.Write([]byte(`<!doctype html><meta charset="utf-8"><title>分享链接不存在</title>
<style>body{font-family:system-ui,sans-serif;margin:4rem auto;max-width:32rem;color:#333}h1{font-size:1.2rem}</style>
<h1>分享链接不存在或已撤销</h1><p>请向分享者确认链接是否仍然有效。</p>`))
}

// sharedPage renders the read-only plan view. html/template escapes every
// interpolated value, so plan text cannot carry markup into the reader's
// browser; resource URLs were already scheme-checked in the kernel.
var sharedPage = template.Must(template.New("shared").Parse(`<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{.Title}} · 分享</title><style>
body{font-family:system-ui,sans-serif;margin:0 auto;max-width:46rem;padding:1.5rem;color:#24292f;line-height:1.65}
header{border-bottom:1px solid #d0d7de;padding-bottom:1rem;margin-bottom:1.5rem}
h1{font-size:1.5rem;margin:0 0 .5rem}h2{font-size:1.15rem;margin:2rem 0 .75rem}h3{font-size:1rem;margin:0 0 .5rem}
.meta{color:#57606a;font-size:.9rem}.stage{border:1px solid #d0d7de;border-radius:8px;padding:1rem;margin:1rem 0}
.res{margin:.25rem 0 .25rem 1.25rem;padding:0}.res li{margin:.2rem 0}ul.topics{columns:2}
.quiz{background:#f6f8fa;border-radius:8px;padding:.75rem 1rem;margin:.75rem 0}
.quiz ol{margin:.4rem 0 0}footer{margin-top:3rem;color:#57606a;font-size:.85rem;border-top:1px solid #d0d7de;padding-top:1rem}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #d0d7de;padding:.35rem .6rem;text-align:left;font-size:.9rem}
</style></head><body>
<header><h1>{{.Title}}</h1>
<div class="meta">{{.Topic}}{{if .Intent.Level}} · {{.Intent.Level}}{{end}}{{if .Intent.DailyMinutes}} · 每天 {{.Intent.DailyMinutes}} 分钟{{end}}{{if .Intent.Deadline}} · 截止 {{.Intent.Deadline}}{{end}} · 分享于 {{.SharedAt}}</div></header>
{{if .Intent.Goal}}<h2>学习目标</h2><p>{{.Intent.Goal}}</p>{{end}}
{{if .Knowledge.Nodes}}<h2>知识地图</h2><ul class="topics">{{range .Knowledge.Nodes}}<li>{{.Name}}<span class="meta">（难度 {{.Difficulty}}{{if .MustLearn}} · 必学{{end}}）</span></li>{{end}}</ul>{{end}}
<h2>学习阶段</h2>
{{range .Stages}}<section class="stage"><h3>{{.Name}}</h3><p>{{.Goal}}</p>
{{if .Topics}}<ul class="topics">{{range .Topics}}<li>{{.Name}}{{if .Why}} — {{.Why}}{{end}}</li>{{end}}</ul>{{end}}
{{if .Resources}}<ul class="res">{{range .Resources}}<li>{{if .URL}}<a href="{{.URL}}" rel="noopener noreferrer">{{.Title}}</a>（{{.Type}} · {{.Verified}}）{{else}}{{.Title}}（{{.Type}} · {{.Verified}}）{{end}}</li>{{end}}</ul>{{end}}
{{if .Milestone}}<p class="meta">里程碑：{{.Milestone}}</p>{{end}}</section>{{end}}
{{if .Schedule}}<h2>日程</h2><table><tr><th>日期</th><th>任务</th><th>预计</th></tr>
{{range .Schedule}}<tr><td>{{.Date}}</td><td>{{range .Tasks}}{{.}}；{{end}}</td><td>{{.EstMinutes}} 分钟</td></tr>{{end}}</table>{{end}}
{{if .Quizzes}}<h2>练习（答案不随分享提供）</h2>{{range .Quizzes}}<div class="quiz"><div>{{.Question}}</div>
{{if .Options}}<ol type="A">{{range .Options}}<li>{{.}}</li>{{end}}</ol>{{end}}</div>{{end}}{{end}}
<footer>由 Reasonix 分享的只读学习方案 · 链接可随时被分享者撤销</footer>
</body></html>`))

func renderSharedPlan(w http.ResponseWriter, view *tutor.SharedPlan) {
	data := struct {
		*tutor.SharedPlan
		SharedAt string
	}{SharedPlan: view, SharedAt: view.CreatedAt.Format("2006-01-02")}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = sharedPage.Execute(w, data)
}
