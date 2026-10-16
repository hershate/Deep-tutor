package jobs

import (
	"context"
	"io"
	"testing"
	"time"
)

func TestAfterUnfinishedWaitsOnlyForJobsRunningNow(t *testing.T) {
	m := NewManager(nil)
	defer m.Close()
	ran := make(chan struct{}, 1)
	m.AfterUnfinished(func() { ran <- struct{}{} })
	select {
	case <-ran:
	default:
		t.Fatal("an empty table must run fn at once")
	}

	release := make(chan struct{})
	m.Start("task", "first", func(ctx context.Context, _ io.Writer) (string, error) {
		<-release
		return "", nil
	})
	m.AfterUnfinished(func() { ran <- struct{}{} })
	stuck := make(chan struct{})
	m.Start("task", "later", func(ctx context.Context, _ io.Writer) (string, error) {
		<-stuck
		return "", nil
	})
	select {
	case <-ran:
		t.Fatal("fn ran while a job it waits on was still running")
	case <-time.After(50 * time.Millisecond):
	}
	close(release)
	select {
	case <-ran:
	case <-time.After(5 * time.Second):
		t.Fatal("fn waited on a job started after it was registered")
	}
	close(stuck)
}
