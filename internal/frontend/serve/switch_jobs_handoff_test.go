package serve

import (
	"context"
	"io"
	"net/http"
	"testing"
	"time"

	"reasonix/internal/session/control"
	"reasonix/internal/tools/jobs"
)

// A job that outlives its turn is not a reason to keep the user on a model
// that cannot answer: the switch goes through and the job keeps running.
func TestModelSwitchWithOnlyBackgroundJobsKeepsTheJobRunning(t *testing.T) {
	mgr := jobs.NewManager(nil)
	t.Cleanup(mgr.Close)
	stopped := make(chan struct{})
	job := mgr.Start("bash", "sleep", func(ctx context.Context, _ io.Writer) (string, error) {
		<-ctx.Done()
		close(stopped)
		return "", ctx.Err()
	})
	srv := newRichProviderServerWith(t, func(c control.SessionAPI) control.SessionAPI { return c },
		func(o *control.Options) { o.Jobs = mgr })

	resp := postProvider(t, srv.URL, "/model", `{"ref":"rich/beta"}`)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNoContent {
		b, _ := readAllString(resp)
		t.Fatalf("POST /model = %d, want success: %s", resp.StatusCode, b)
	}

	select {
	case <-stopped:
		t.Fatal("the switch cancelled the background job")
	case <-time.After(300 * time.Millisecond):
	}
	if running := mgr.Running(); len(running) != 1 || running[0].ID != job.ID {
		t.Fatalf("running jobs after the switch = %v, want only %s", running, job.ID)
	}
}
