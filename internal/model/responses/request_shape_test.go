package responses

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"reasonix/internal/contract/provider"
)

const gatewayBody = `{"error":{"message":"Invalid JSON data: input: data did not match any variant of untagged enum ResponseInput at line 1 column 827355","code":"json_parse_error"}}`

// A 400 keeps the gateway's own words and gains what the host knows of the body
// it sent, never the content.
func TestRefusedRequestCarriesItsShapeBesideTheGatewaysWords(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(gatewayBody))
	}))
	defer srv.Close()
	client := New(Config{Name: "gw", BaseURL: srv.URL, Model: "m", APIKey: "k"}).(*client)
	_, err := client.Stream(context.Background(), provider.Request{Messages: []provider.Message{
		{Role: provider.RoleUser, Content: "SECRET-PROMPT"},
		{Role: provider.RoleAssistant, ToolCalls: []provider.ToolCall{{ID: "c1", Name: "read", Arguments: "{}"}}},
		{Role: provider.RoleTool, ToolCallID: "c1", Content: strings.Repeat("x", 5000)},
	}})
	var apiErr *provider.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusBadRequest {
		t.Fatalf("err = %v, want a 400 APIError", err)
	}
	if apiErr.Body != gatewayBody {
		t.Fatalf("the gateway's words were changed: %q", apiErr.Body)
	}
	for _, want := range []string{"Request shape:", "3 items", "function_call 1", "function_call_output 1", "message 1", "largest is input[2], a function_call_output"} {
		if !strings.Contains(apiErr.ToolContext, want) {
			t.Errorf("shape %q lacks %q", apiErr.ToolContext, want)
		}
	}
	if strings.Contains(apiErr.ToolContext, "SECRET-PROMPT") {
		t.Fatal("the shape carries request content")
	}
}

func TestOtherStatusesAreNotAnnotated(t *testing.T) {
	err := provider.AnnotateRequestShape(&provider.APIError{Status: 500, Body: "x"}, "Request shape: y.")
	var apiErr *provider.APIError
	if !errors.As(err, &apiErr) || apiErr.ToolContext != "" {
		t.Fatalf("a 500 was annotated: %+v", apiErr)
	}
}
