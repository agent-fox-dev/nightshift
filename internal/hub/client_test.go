package hub_test

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"slices"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
)

func tokenID(t *testing.T, pat string) string {
	t.Helper()
	id, ok := hub.TokenID(pat)
	if !ok {
		t.Fatalf("TokenID(%q) refused a personal access token", pat)
	}
	return id
}

func TestTokenID(t *testing.T) {
	t.Parallel()
	tests := []struct {
		pat, id string
		ok      bool
	}{
		{"parley_pat_tok1_secret", "tok1", true},
		{"parley_pat_tok1_sec_ret", "tok1", true},
		{"parley_pat__secret", "", false},
		{"parley_pat_tok1_", "", false},
		{"parley_pat_tok1", "", false},
		{"parley_ab_cd", "", false},
		{"parley_admin_xyz", "", false},
		{"", "", false},
	}
	for _, tt := range tests {
		t.Run(tt.pat, func(t *testing.T) {
			t.Parallel()
			id, ok := hub.TokenID(tt.pat)
			if id != tt.id || ok != tt.ok {
				t.Errorf("TokenID(%q) = %q, %v; want %q, %v", tt.pat, id, ok, tt.id, tt.ok)
			}
		})
	}
}

func TestClient_DoSendsTheNamedCredential(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	c := hub.New(h.URL(), worker, approver)

	for _, tt := range []struct {
		cred   hub.Credential
		name   string
		bearer string
	}{
		{hub.Worker, "worker", worker},
		{hub.Approver, "approver", approver},
	} {
		if got := tt.cred.String(); got != tt.name {
			t.Errorf("String() = %q, want %q", got, tt.name)
		}
		req, err := c.NewRequest(t.Context(), http.MethodGet, "/api/v1/elsewhere", nil)
		if err != nil {
			t.Fatal(err)
		}
		if resp, err := c.Do(t.Context(), tt.cred, req); err == nil {
			resp.Body.Close()
		}
		if n := len(h.Bearing(tt.bearer)); n != 1 {
			t.Errorf("%s: the hub saw %d requests bearing its token, want 1", tt.name, n)
		}
	}
}

func TestClient_TokenReadsTheTokenObject(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	bound := h.AddToken(hubtest.Token{Permissions: []string{"tokens:read"}, Workspace: "ws1", ReadsBound: true})
	c := hub.New(h.URL(), worker, approver)

	tok, err := c.Token(t.Context(), hub.Worker, tokenID(t, approver))
	if err != nil {
		t.Fatalf("Token: %v", err)
	}
	if tok.TokenID != tokenID(t, approver) || tok.UserID != hubtest.Owner {
		t.Errorf("token_id, user_id = %q, %q; want %q, %q", tok.TokenID, tok.UserID, tokenID(t, approver), hubtest.Owner)
	}
	if !slices.Equal(tok.Permissions, hubtest.ApproverScopes()) {
		t.Errorf("permissions = %v, want %v", tok.Permissions, hubtest.ApproverScopes())
	}
	if !tok.Holds("changes:approve") || tok.Holds("changes") {
		t.Errorf("Holds: want changes:approve held and changes, a prefix, not held")
	}
	if tok.Bound() {
		t.Errorf("an unbound token reports Bound")
	}

	tok, err = c.WithTokens(bound, approver).Token(t.Context(), hub.Worker, tokenID(t, bound))
	if err != nil {
		t.Fatalf("Token of a bound token: %v", err)
	}
	if !tok.Bound() {
		t.Errorf("a token with a workspace does not report Bound")
	}
}

func TestClient_DecodesTheErrorEnvelope(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name   string
		token  hubtest.Token
		read   func(own string) string // the token id read
		status int
		want   string
	}{
		{"revoked", hubtest.Token{Permissions: []string{"tokens:read"}, Revoked: true}, nil, 401, "unauthenticated"},
		{"bound", hubtest.Token{Permissions: []string{"tokens:read"}, Workspace: "ws1"}, nil, 403, "token_bound"},
		{"no scope", hubtest.Token{Permissions: []string{"git:read"}}, nil, 403, "insufficient_scope"},
		{"unknown id", hubtest.Token{Permissions: []string{"tokens:read"}}, func(string) string { return "nosuch" }, 404, "not_found"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			pat := h.AddToken(tt.token)
			id := tokenID(t, pat)
			if tt.read != nil {
				id = tt.read(id)
			}
			_, err := hub.New(h.URL(), pat, "").Token(t.Context(), hub.Worker, id)
			var he *hub.Error
			if !errors.As(err, &he) {
				t.Fatalf("Token error = %v, want a *hub.Error", err)
			}
			if he.Status != tt.status || he.Code != tt.want || he.ErrorType != tt.want || he.Message == "" {
				t.Errorf("error = %+v, want status %d, code and error_type %q and a message", he, tt.status, tt.want)
			}
			if !he.Named(tt.want) || he.Named("other") {
				t.Errorf("Named(%q) = %v, Named(other) = %v", tt.want, he.Named(tt.want), he.Named("other"))
			}
			if got, want := he.Error(), fmt.Sprintf("%d %s", tt.status, tt.want); got != want {
				t.Errorf("Error() = %q, want %q", got, want)
			}
		})
	}
}

func TestClient_Ready(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name   string
		status int
		want   int // the *hub.Error's status, or 0 for none
	}{
		{"ready", http.StatusOK, 0},
		{"not ready", http.StatusServiceUnavailable, http.StatusServiceUnavailable},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			h.SetReady(tt.status)
			err := hub.New(h.URL(), "", "").Ready(context.Background())
			var he *hub.Error
			switch {
			case tt.want == 0 && err != nil:
				t.Errorf("Ready = %v, want nil", err)
			case tt.want != 0 && (!errors.As(err, &he) || he.Status != tt.want):
				t.Errorf("Ready = %v, want a *hub.Error with status %d", err, tt.want)
			}
			if r := h.Requests(); len(r) != 1 || r[0].Path != "/readyz" || r[0].Bearer != "" {
				t.Errorf("requests = %+v, want one GET /readyz without a credential", r)
			}
		})
	}
}
