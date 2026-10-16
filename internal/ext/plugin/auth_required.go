package plugin

import (
	"errors"
	"fmt"
	"net/http"

	"reasonix/internal/base/secrets"
	"reasonix/internal/contract/mcpdiag"
)

// ErrAuthenticationRequired is the identity of a remote MCP server that
// answered 401 with a Bearer challenge: the user can fix it by signing in, and
// retrying without doing so cannot succeed.
var ErrAuthenticationRequired = errors.New("MCP server requires authentication")

// AuthRequiredError carries which server and resource asked for sign-in. It
// wraps the HTTP answer, so the status stays readable through errors.As.
type AuthRequiredError struct {
	Server   string
	Resource string
	Scope    string
	cause    *httpStatusError
}

func (e *AuthRequiredError) Error() string {
	return fmt.Sprintf("MCP server %q requires authentication: %v", e.Server, e.cause)
}

func (e *AuthRequiredError) Unwrap() []error { return []error{ErrAuthenticationRequired, e.cause} }

func (e *AuthRequiredError) DiagnosticFacts() string { return e.Error() }

// statusFailure builds the error for a non-2xx answer, promoting a 401 that
// carries a Bearer challenge to AuthRequiredError. A server with explicit credentials
// configured is not asking for a sign-in: its token is wrong.
func (t *httpTransport) statusFailure(resp *http.Response, bodyBytes int, rpc *rpcError) error {
	status := &httpStatusError{Status: resp.StatusCode, BodyBytes: bodyBytes, RPC: rpc}
	if resp.StatusCode != http.StatusUnauthorized || mcpdiag.HasAuthConfig(t.headers, nil, t.url) {
		return status
	}
	for _, challenge := range resp.Header.Values("WWW-Authenticate") {
		if _, scope, ok := parseBearerChallenge(challenge); ok {
			return &AuthRequiredError{Server: t.name, Resource: secrets.RedactEndpoint(t.url), Scope: scope, cause: status}
		}
	}
	return status
}
