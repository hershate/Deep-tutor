package boot

import (
	"context"
	"io"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
)

// A replacement built on the outgoing controller's job manager and write lease
// sees the job the old one started, and retiring the old controller leaves it
// running.
func TestEffectRebuildAdoptingJobsKeepsARunningJob(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	provider.Register("jobs-handoff", func(provider.Config) (provider.Provider, error) {
		return &effectRecordingProvider{}, nil
	})
	writeFile(t, dir, "reasonix.toml", `
default_model = "test-model"

[[providers]]
name = "test-model"
kind = "jobs-handoff"
model = "x"
`)
	approveWorkspace(t, dir)

	oldCtrl, err := Build(context.Background(), Options{Sink: event.Discard})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	job := oldCtrl.JobManager().Start("bash", "sleep", func(ctx context.Context, _ io.Writer) (string, error) {
		<-ctx.Done()
		return "", ctx.Err()
	})

	res, err := BuildRuntime(context.Background(), Options{
		Sink: event.Discard, Jobs: oldCtrl.JobManager(), WorkspaceLease: oldCtrl.WorkspaceLeaseOwner(),
	})
	if err != nil {
		t.Fatalf("BuildRuntime: %v", err)
	}
	newCtrl := res.Controller
	defer newCtrl.Close()
	if !newCtrl.SharesJobsWith(oldCtrl) || newCtrl.WorkspaceLeaseOwner() != oldCtrl.WorkspaceLeaseOwner() {
		t.Fatal("the replacement did not adopt the job manager and the write lease")
	}

	oldCtrl.CloseHandingOffJobs()
	time.Sleep(200 * time.Millisecond)
	if running := newCtrl.JobManager().Running(); len(running) != 1 || running[0].ID != job.ID {
		t.Fatalf("running jobs after the old controller retired = %v, want %s", running, job.ID)
	}
}
