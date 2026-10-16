package jobs

import (
	"context"
	"io"
	"strings"
	"sync"
	"testing"
	"time"

	"reasonix/internal/contract/event"
)

type echoed struct {
	mu   sync.Mutex
	text strings.Builder
}

func (e *echoed) add(s string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.text.WriteString(s)
}

func (e *echoed) String() string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.text.String()
}

// A wait is the longest silence a person watches: the job is working and the
// row says only that something is being waited for. What the job writes while
// it lasts is echoed, so the row shows the work rather than a clock.
func TestWaitEchoesWhatTheJobWritesWhileItWaits(t *testing.T) {
	m := NewManager(event.Discard)
	defer m.Close()

	step := make(chan struct{})
	j := m.Start("bash", "build", func(ctx context.Context, w io.Writer) (string, error) {
		_, _ = io.WriteString(w, "compiling 1/3\n")
		<-step
		_, _ = io.WriteString(w, "compiling 2/3\n")
		_, _ = io.WriteString(w, "compiling 3/3\nlinking")
		_, _ = io.WriteString(w, " done\n")
		return "", nil
	})

	var got echoed
	go func() {
		time.Sleep(100 * time.Millisecond)
		close(step)
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, outcome := m.WaitForSession(ctx, "", []string{j.ID}, WaitOptions{Echo: got.add})
	if outcome != WaitFinished {
		t.Fatalf("outcome = %q", outcome)
	}
	want := "compiling 1/3\ncompiling 2/3\ncompiling 3/3\nlinking done\n"
	if got.String() != want {
		t.Fatalf("echo = %q, want %q", got.String(), want)
	}
}

// Two jobs interleave, so each line says whose it is; one job needs no name.
func TestWaitEchoNamesTheJobWhenItWaitsForSeveral(t *testing.T) {
	m := NewManager(event.Discard)
	defer m.Close()

	release := make(chan struct{})
	a := m.Start("bash", "a", func(ctx context.Context, w io.Writer) (string, error) {
		<-release
		_, _ = io.WriteString(w, "from a\n")
		return "", nil
	})
	b := m.Start("bash", "b", func(ctx context.Context, w io.Writer) (string, error) {
		<-release
		_, _ = io.WriteString(w, "from b\n")
		return "", nil
	})
	var got echoed
	go func() {
		time.Sleep(50 * time.Millisecond)
		close(release)
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	m.WaitForSession(ctx, "", []string{a.ID, b.ID}, WaitOptions{Echo: got.add})
	out := got.String()
	if !strings.Contains(out, "["+a.ID+"] from a\n") || !strings.Contains(out, "["+b.ID+"] from b\n") {
		t.Fatalf("echo = %q, want each line under its job", out)
	}
}

// A multi-byte character split across two writes reaches the reader whole.
func TestWaitEchoNeverSplitsACharacter(t *testing.T) {
	m := NewManager(event.Discard)
	defer m.Close()

	word := []byte("编译完成\n")
	release := make(chan struct{})
	j := m.Start("bash", "zh", func(ctx context.Context, w io.Writer) (string, error) {
		<-release
		_, _ = w.Write(word[:4])
		time.Sleep(20 * time.Millisecond)
		_, _ = w.Write(word[4:])
		return "", nil
	})
	var mu sync.Mutex
	var chunks []string
	go func() {
		time.Sleep(50 * time.Millisecond)
		close(release)
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	m.WaitForSession(ctx, "", []string{j.ID}, WaitOptions{Echo: func(s string) {
		mu.Lock()
		chunks = append(chunks, s)
		mu.Unlock()
	}})
	if strings.Join(chunks, "") != string(word) {
		t.Fatalf("chunks = %q", chunks)
	}
	for _, c := range chunks {
		if strings.ContainsRune(c, '�') || !strings.HasSuffix(c, "\n") && c != "" {
			t.Fatalf("chunk %q is not whole lines", c)
		}
	}
}

// The live echo is a view, capped like a shell's, so a chatty job cannot
// flood the transcript; the wait's own result is unaffected.
func TestWaitEchoIsCapped(t *testing.T) {
	m := NewManager(event.Discard)
	defer m.Close()

	release := make(chan struct{})
	line := strings.Repeat("x", 1023) + "\n"
	j := m.Start("bash", "loud", func(ctx context.Context, w io.Writer) (string, error) {
		<-release
		for range 200 {
			_, _ = io.WriteString(w, line)
		}
		return "", nil
	})
	var got echoed
	go func() {
		time.Sleep(50 * time.Millisecond)
		close(release)
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	m.WaitForSession(ctx, "", []string{j.ID}, WaitOptions{Echo: got.add})
	out := got.String()
	if len(out) > echoLimit+len(echoCapped) {
		t.Fatalf("echo is %d bytes, over the %d cap", len(out), echoLimit)
	}
	if !strings.HasSuffix(out, echoCapped) {
		t.Fatalf("echo does not say it was capped: %q", out[len(out)-80:])
	}
}

// Output with no newline — a progress bar, a binary stream — is still shown,
// in bounded pieces, and none of it is dropped for an invalid byte.
func TestWaitEchoFlushesALineThatNeverEnds(t *testing.T) {
	m := NewManager(event.Discard)
	defer m.Close()

	release := make(chan struct{})
	bar := strings.Repeat("#", 3*echoOpenLimit) + "abc\xffdef"
	j := m.Start("bash", "bar", func(ctx context.Context, w io.Writer) (string, error) {
		<-release
		for i := 0; i < len(bar); i += 100 {
			_, _ = io.WriteString(w, bar[i:min(i+100, len(bar))])
		}
		return "", nil
	})
	var got echoed
	go func() {
		time.Sleep(50 * time.Millisecond)
		close(release)
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	m.WaitForSession(ctx, "", []string{j.ID}, WaitOptions{Echo: got.add})
	if out := strings.ReplaceAll(got.String(), "\n", ""); out != bar {
		t.Fatalf("echo lost bytes: got %d, want %d (tail %q)", len(out), len(bar), out[max(0, len(out)-12):])
	}
}

func TestCompleteRunesKeepsEverythingButASplitCharacter(t *testing.T) {
	zh := []byte("编")
	for _, c := range []struct{ in, want string }{
		{"abc\xffdef", "abc\xffdef"},
		{"ab" + string(zh[:2]), "ab"},
		{"ab" + string(zh), "ab" + string(zh)},
		{"", ""},
	} {
		if got := string(completeRunes([]byte(c.in))); got != c.want {
			t.Errorf("completeRunes(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}
