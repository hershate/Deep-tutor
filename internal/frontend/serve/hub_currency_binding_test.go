package serve

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/assembly/boot"
	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/eventwire"
	"reasonix/internal/contract/provider"
	"reasonix/internal/session/control"
)

func TestHubCurrencyBindingReconcilesBuiltLocalProjections(t *testing.T) {
	wallet := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"10.00"},{"currency":"USD","total_balance":"2.00"}]}`))
	}))
	defer wallet.Close()
	writeWalletConfig(t, wallet.URL)
	path := config.UserConfigPath()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	seed := "config_version = 12\n" + strings.ReplaceAll(strings.ReplaceAll(string(raw), "shared-chat", "deepseek-flash"), "http://127.0.0.1:1/v1", "https://api.deepseek.com/v1")
	setCurrency := func(currency string) {
		t.Helper()
		if err := os.WriteFile(path, []byte(seed+"\n[billing]\ndisplay_currency = \""+currency+"\"\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	setCurrency("CNY")
	usage := event.Event{Kind: event.Usage, ModelRef: "default/deepseek-flash", Usage: &provider.Usage{PromptTokens: 1000, CompletionTokens: 1000}, Pricing: &provider.Pricing{Currency: "CNY", CacheHit: .02, Input: 1, Output: 4}}
	servers := make([]*Server, 0, 3)
	for range 3 {
		root, bc := testenv.TempDir(t), NewBroadcaster()
		built, err := boot.BuildRuntime(t.Context(), boot.Options{WorkspaceRoot: root, SessionDir: SessionDirFor(root), Sink: bc})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(built.Controller.Close)
		s := New(built.Controller, bc, config.ServeConfig{})
		s.AdoptRuntime(built)
		built.Controller.EmitExtensionEvent(usage)
		servers = append(servers, s)
	}
	// The fixture isolates publication after a configuration change during build.
	setCurrency("USD")
	h := NewHub(HubOptions{})
	defer h.Shutdown()
	far := fakeRemoteKernel(t)
	remotePane(t, h, far.Listener.Addr().String())
	httpServer := httptest.NewServer(operatorHandler(h))
	defer httpServer.Close()
	var runtimes []*Runtime
	for _, s := range servers {
		beforeBind := s.bc.Watermark()
		rt, err := h.Adopt(s, s.bc)
		if err != nil {
			t.Fatal(err)
		}
		runtimes = append(runtimes, rt)
		if s.bc.Watermark() != beforeBind {
			t.Error("quiet binding emitted a visible notice")
		}
		if got := s.bc.DisplayCurrency(); got != "USD" {
			t.Errorf("%s ledger currency = %q, want USD", rt.ID, got)
		}
		if q := s.bc.SessionCostQuote(); q.Selected == nil || q.Selected.Currency != "USD" {
			t.Errorf("%s ledger = %+v, want USD", rt.ID, q)
		}
		before := s.bc.Watermark()
		s.Controller().(*control.Controller).EmitExtensionEvent(usage)
		frames, _ := s.bc.Replay(before)
		var wire eventwire.Event
		if len(frames) != 1 {
			t.Fatalf("usage frames = %d, want one", len(frames))
		}
		if err := json.Unmarshal(frames[0], &wire); err != nil {
			t.Fatal(err)
		}
		if wire.Usage == nil || wire.Usage.CostQuote == nil || wire.Usage.CostQuote.Selected == nil || wire.Usage.CostQuote.Selected.Currency != "USD" {
			t.Errorf("%s future usage = %+v, want USD quote", rt.ID, wire.Usage)
		}
		if balance := hubGet[walletBody](t, httpServer, "/rt/"+rt.ID+"/balance"); balance.Display != "$2.00" {
			t.Errorf("%s balance = %q, want $2.00", rt.ID, balance.Display)
		}
		if s.bc.Watermark() != before+1 {
			t.Error("quiet bind emitted an extra notice")
		}
	}
	s := servers[0]
	old := s.Controller()
	s.buildController = func(ctx context.Context, ref string) (*control.Controller, error) {
		built, err := boot.Build(ctx, s.rebuildOptions(s.Controller(), ref))
		if err == nil {
			setCurrency("CNY")
		}
		return built, err
	}
	before := s.bc.Watermark()
	if err := s.switchModel(t.Context(), "default/deepseek-flash"); err != nil {
		t.Fatal(err)
	}
	if s.Controller() == old || s.bc.DisplayCurrency() != "CNY" {
		t.Fatal("replacement missed canonical currency")
	}
	if s.bc.Watermark() != before {
		t.Fatal("replacement emitted a duplicate currency notice")
	}
	if err := old.SaveDisplayCurrency("USD"); err == nil {
		t.Fatal("retired generation saved currency")
	}
	s.Controller().(*control.Controller).EmitExtensionEvent(usage)
	frames, _ := s.bc.Replay(before)
	var replacementUsage eventwire.Event
	if len(frames) != 1 {
		t.Fatalf("replacement frames = %d", len(frames))
	}
	if err := json.Unmarshal(frames[0], &replacementUsage); err != nil {
		t.Fatal(err)
	}
	if replacementUsage.Usage == nil || replacementUsage.Usage.CostQuote.Selected.Currency != "CNY" {
		t.Fatal("replacement's full quote sink stayed stale")
	}
	current := s.Controller()
	if err := h.Close(runtimes[0].ID); err != nil {
		t.Fatal(err)
	}
	if err := current.SaveDisplayCurrency("USD"); err == nil {
		t.Fatal("closed runtime saved currency")
	}
	if err := s.switchModel(t.Context(), "default/deepseek-flash"); err == nil {
		t.Fatal("closed runtime published a queued replacement")
	}
	if s.Controller() != current || h.Get(runtimes[0].ID) != nil {
		t.Fatal("closed runtime was resurrected")
	}
	select {
	case cookie := <-far.cookies:
		t.Fatalf("local currency reached remote: %q", cookie)
	default:
	}
}

func TestHubCurrencyBindingPublishesWithInvalidConfig(t *testing.T) {
	for _, transition := range []string{"adopt", "open", "open-defaults", "model", "extensions", "workspace"} {
		t.Run(transition, func(t *testing.T) {
			t.Setenv("REASONIX_HOME", testenv.TempDir(t))
			closeSharedCatalogsOnCleanup(t)
			path := config.UserConfigPath()
			seed := []byte("config_version = 12\n[billing]\ndisplay_currency = \"USD\"\n")
			if err := os.WriteFile(path, seed, 0600); err != nil {
				t.Fatal(err)
			}
			if transition != "open-defaults" {
				if err := os.MkdirAll(filepath.Dir(config.LastKnownGoodConfigPath()), 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(config.LastKnownGoodConfigPath(), seed, 0600); err != nil {
					t.Fatal(err)
				}
			}
			resolver := homeResolver()
			root, bc := testenv.TempDir(t), NewBroadcaster()
			built, err := boot.BuildRuntime(t.Context(), boot.Options{WorkspaceRoot: root, SessionDir: SessionDirFor(root), Sink: bc, ProviderResolver: resolver})
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(built.Controller.Close)
			s := New(built.Controller, bc, config.ServeConfig{})
			s.AdoptRuntime(built)
			h := NewHub(HubOptions{ProviderResolver: resolver})
			t.Cleanup(h.Shutdown)
			if transition != "adopt" && transition != "open" && transition != "open-defaults" {
				if _, err := h.Adopt(s, bc); err != nil {
					t.Fatal(err)
				}
			}
			const malformed = "garbage = = =\n"
			if err := os.WriteFile(path, []byte(malformed), 0600); err != nil {
				t.Fatal(err)
			}
			switch transition {
			case "adopt":
				_, err = h.Adopt(s, bc)
			case "open", "open-defaults":
				var rt *Runtime
				rt, err = h.Open(t.Context(), OpenRequest{Root: testenv.TempDir(t)})
				if err == nil {
					s, bc = rt.Server, rt.Events
				}
			case "model":
				err = s.switchModel(t.Context(), currentModelRef(s.Controller()))
			case "extensions":
				err = s.reloadExtensions(t.Context())
			case "workspace":
				s.bindMu.Lock()
				err = s.switchWorkspaceLocked(t.Context(), testenv.TempDir(t))
				s.bindMu.Unlock()
			}
			if err != nil {
				t.Fatalf("publish with malformed config: %v", err)
			}
			if raw, err := os.ReadFile(path); err != nil || string(raw) != malformed {
				t.Fatalf("malformed config changed: %q, %v", raw, err)
			}
			wantDisplay, wantQuote := "USD", "USD"
			if transition == "open-defaults" {
				wantDisplay, wantQuote = "", "CNY"
			}
			if got := bc.DisplayCurrency(); got != wantDisplay {
				t.Fatalf("built broadcaster projection = %q, want %q", got, wantDisplay)
			}
			before := bc.Watermark()
			s.Controller().(*control.Controller).EmitExtensionEvent(event.Event{Kind: event.Usage, ModelRef: "deepseek/deepseek-flash", Usage: &provider.Usage{PromptTokens: 1000}, Pricing: &provider.Pricing{Currency: "CNY", CacheHit: .02, Input: 1, Output: 4}})
			frames, _ := bc.Replay(before)
			var wire eventwire.Event
			if len(frames) != 1 {
				t.Fatalf("usage frames = %d, want one", len(frames))
			}
			if err := json.Unmarshal(frames[0], &wire); err != nil {
				t.Fatal(err)
			}
			if wire.Usage == nil || wire.Usage.CostQuote == nil || wire.Usage.CostQuote.Selected == nil || wire.Usage.CostQuote.Selected.Currency != wantQuote {
				t.Fatalf("built future quote projection = %+v, want %s", wire.Usage, wantQuote)
			}
		})
	}
}
