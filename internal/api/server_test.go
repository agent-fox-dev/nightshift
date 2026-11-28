package api_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/api"
	"github.com/agent-fox-dev/nightshift/internal/buildinfo"
)

var clientToken = strings.Repeat("0a", 32)

// stubHub reports a fixed hub state and counts how often it was asked, so
// that a test sees whether the status handler ran.
type stubHub struct {
	state api.HubState
	calls atomic.Int32
}

func (h *stubHub) HubState() api.HubState {
	h.calls.Add(1)
	return h.state
}

func reachableHub() *stubHub {
	return &stubHub{state: api.HubState{Endpoint: "https://hub.example", Reachable: true}}
}

// do sends a request through the server's handler, with the client token
// when authed.
func do(s http.Handler, method, target string, authed bool) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, target, nil)
	if authed {
		r.Header.Set("Authorization", "Bearer "+clientToken)
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	return w
}

type errorBody struct {
	ErrorType string `json:"error_type"`
	Message   string `json:"message"`
}

// assertError checks that w is a JSON error answer with status and
// errorType.
func assertError(t *testing.T, w *httptest.ResponseRecorder, status int, errorType string) {
	t.Helper()
	if w.Code != status {
		t.Errorf("status = %d, want %d (body %q)", w.Code, status, w.Body.String())
	}
	if ct := w.Header().Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}
	var body errorBody
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("body %q is not JSON: %v", w.Body.String(), err)
	}
	if body.ErrorType != errorType {
		t.Errorf("error_type = %q, want %q", body.ErrorType, errorType)
	}
	if body.Message == "" {
		t.Errorf("message is empty")
	}
}

func TestServer_RejectsRequestsWithoutTheClientToken(t *testing.T) {
	t.Parallel()
	credentials := []struct {
		name          string
		authorization string
		query         string
	}{
		{name: "no header"},
		{name: "wrong bearer", authorization: "Bearer wrong"},
		{name: "basic credential", authorization: "Basic " + clientToken},
		{name: "token in the query", query: "?token=" + clientToken},
	}
	for _, path := range []string{"/api/v1/status", "/api/v1/nope"} {
		for _, c := range credentials {
			t.Run(c.name+" "+path, func(t *testing.T) {
				t.Parallel()
				hub := reachableHub()
				s := api.New(clientToken, hub)
				r := httptest.NewRequest(http.MethodGet, path+c.query, nil)
				if c.authorization != "" {
					r.Header.Set("Authorization", c.authorization)
				}
				w := httptest.NewRecorder()

				s.ServeHTTP(w, r)

				assertError(t, w, http.StatusUnauthorized, "unauthorized")
				if n := hub.calls.Load(); n != 0 {
					t.Errorf("the status handler ran %d times, want never", n)
				}
			})
		}
	}
}

func TestServer_AnswersErrorsAsJSON(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name           string
		method, target string
		status         int
		errorType      string
	}{
		{"unknown path", http.MethodGet, "/api/v1/nope", http.StatusNotFound, "not_found"},
		{"wrong method", http.MethodPost, "/api/v1/status", http.StatusMethodNotAllowed, "method_not_allowed"},
		{"handler panics", http.MethodGet, "/api/v1/panic", http.StatusInternalServerError, "internal"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			s := api.New(clientToken, reachableHub())
			s.Handle("GET /api/v1/panic", http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				panic("boom")
			}))

			assertError(t, do(s, tt.method, tt.target, true), tt.status, tt.errorType)

			if w := do(s, http.MethodGet, "/api/v1/status", true); w.Code != http.StatusOK {
				t.Errorf("the next request answered %d, want 200", w.Code)
			}
		})
	}
}

func TestStatus_ReportsVersionHubAndSources(t *testing.T) {
	t.Parallel()
	since := time.Date(2026, 11, 28, 10, 0, 0, 0, time.UTC)
	tests := []struct {
		name           string
		state          api.HubState
		sessions       api.StatusSource
		wantHub        map[string]any
		wantSessions   []any
		wantWorkspaces []any
	}{
		{
			name:           "reachable hub and no sources",
			state:          api.HubState{Endpoint: "https://hub.example", Reachable: true},
			wantHub:        map[string]any{"endpoint": "https://hub.example", "reachable": true, "unreachable_since": nil, "credential_error": nil},
			wantSessions:   []any{},
			wantWorkspaces: []any{},
		},
		{
			name:           "unreachable hub with a credential error",
			state:          api.HubState{Endpoint: "https://hub.example", UnreachableSince: since, CredentialError: "worker token: 401 unauthenticated"},
			wantHub:        map[string]any{"endpoint": "https://hub.example", "reachable": false, "unreachable_since": "2026-11-28T10:00:00Z", "credential_error": "worker token: 401 unauthenticated"},
			wantSessions:   []any{},
			wantWorkspaces: []any{},
		},
		{
			name:  "a registered sessions source",
			state: api.HubState{Endpoint: "https://hub.example", Reachable: true},
			sessions: func(context.Context) (any, error) {
				return []map[string]string{{"id": "s1"}}, nil
			},
			wantHub:        map[string]any{"endpoint": "https://hub.example", "reachable": true, "unreachable_since": nil, "credential_error": nil},
			wantSessions:   []any{map[string]any{"id": "s1"}},
			wantWorkspaces: []any{},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			s := api.New(clientToken, &stubHub{state: tt.state})
			if tt.sessions != nil {
				s.SetStatusSource("sessions", tt.sessions)
			}

			w := do(s, http.MethodGet, "/api/v1/status", true)

			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200 (body %q)", w.Code, w.Body.String())
			}
			var body map[string]any
			if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
				t.Fatalf("body %q is not JSON: %v", w.Body.String(), err)
			}
			if body["version"] != buildinfo.Version {
				t.Errorf("version = %v, want %q", body["version"], buildinfo.Version)
			}
			if !reflect.DeepEqual(body["hub"], tt.wantHub) {
				t.Errorf("hub = %v, want %v", body["hub"], tt.wantHub)
			}
			if !reflect.DeepEqual(body["sessions"], tt.wantSessions) {
				t.Errorf("sessions = %#v, want %#v", body["sessions"], tt.wantSessions)
			}
			if !reflect.DeepEqual(body["workspaces"], tt.wantWorkspaces) {
				t.Errorf("workspaces = %#v, want %#v", body["workspaces"], tt.wantWorkspaces)
			}
			if strings.Contains(w.Body.String(), clientToken) {
				t.Errorf("body %q holds the client token", w.Body.String())
			}
		})
	}
}
