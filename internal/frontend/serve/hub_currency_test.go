package serve

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
)

func TestHubCurrencyRebindsEveryLocalRuntime(t *testing.T) {
	wallet := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"10.00"},{"currency":"USD","total_balance":"2.00"}]}`))
	}))
	defer wallet.Close()
	writeWalletConfig(t, wallet.URL)
	h := NewHub(HubOptions{})
	defer h.Shutdown()
	far := fakeRemoteKernel(t)
	remotePane(t, h, far.Listener.Addr().String())
	runtimes := make([]*Runtime, 0, 3)
	for range 3 {
		rt, err := h.Open(t.Context(), OpenRequest{Root: testenv.TempDir(t)})
		if err != nil {
			t.Fatal(err)
		}
		rt.Events.Emit(event.Event{Kind: event.Usage, Usage: &provider.Usage{PromptTokens: 1000}, CostQuote: event.EnsureCostQuote(event.Event{ModelRef: "deepseek/deepseek-flash", Usage: &provider.Usage{PromptTokens: 1000}, Pricing: &provider.Pricing{Currency: "CNY", CacheHit: 0.02, Input: 1, Output: 4}}, &event.QuoteContext{CatalogProviderForModel: func(string) string { return "deepseek" }})})
		runtimes = append(runtimes, rt)
	}
	handler := operatorHandler(h)
	request := func(method, path, body string) *httptest.ResponseRecorder {
		t.Helper()
		r := httptest.NewRequest(method, "http://localhost"+path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w
	}
	w := request(http.MethodPost, "/host/settings/display-currency", `{"mode":"USD"}`)
	if w.Code != http.StatusOK {
		t.Fatalf("save = %d %s", w.Code, w.Body.String())
	}
	for _, rt := range runtimes {
		if got := rt.Events.DisplayCurrency(); got != "USD" {
			t.Errorf("%s live currency = %q, want USD", rt.ID, got)
		}
		if q := rt.Events.SessionCostQuote(); q.Selected == nil || q.Selected.Currency != "USD" {
			t.Errorf("%s cost = %+v, want USD", rt.ID, q)
		}
		w = request(http.MethodGet, "/rt/"+rt.ID+"/balance", "")
		var balance walletBody
		if err := json.Unmarshal(w.Body.Bytes(), &balance); err != nil {
			t.Fatal(err)
		}
		if balance.Display != "$2.00" {
			t.Errorf("%s balance = %q, want $2.00", rt.ID, balance.Display)
		}
	}
	select {
	case cookie := <-far.cookies:
		t.Fatalf("currency reached remote: %q", cookie)
	default:
	}
}
