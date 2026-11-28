package workspace_test

import (
	"context"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/hubtest"
	"github.com/agent-fox-dev/nightshift/internal/policy"
	"github.com/agent-fox-dev/nightshift/internal/workspace"
)

// inserted is the workspace insert stores, as a registration with the
// health command make check and the defaults would.
func inserted(slug string) workspace.Workspace {
	return workspace.Workspace{
		Slug:          slug,
		GitURL:        "http://hub.invalid/git/acme/" + slug + ".git",
		HealthCommand: "make check",
		HealthTimeout: 30 * time.Minute,
		DailyTokens:   50_000_000,
		State:         workspace.Green,
		CreatedAt:     time.Date(2026, 11, 28, 10, 0, 0, 0, time.UTC),
	}
}

// insert stores the workspace slug directly, without its registration.
func (f *fixture) insert(t *testing.T, slug string) {
	t.Helper()
	w := inserted(slug)
	_, err := f.st.DB().Exec(`INSERT INTO workspaces
		(slug, git_url, health_command, health_timeout_ns, daily_tokens, state, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		w.Slug, w.GitURL, w.HealthCommand, int64(w.HealthTimeout), w.DailyTokens, string(w.State),
		w.CreatedAt.Format(time.RFC3339))
	if err != nil {
		t.Fatalf("inserting %s: %v", slug, err)
	}
}

// recordRule stores the name of a rule protect wrote for slug.
func (f *fixture) recordRule(t *testing.T, slug, name string) {
	t.Helper()
	if _, err := f.st.DB().Exec(`INSERT INTO workspace_rules (slug, name) VALUES (?, ?)`, slug, name); err != nil {
		t.Fatalf("recording the rule %s of %s: %v", name, slug, err)
	}
}

// recordedRules is the number of rule names stored for slug.
func (f *fixture) recordedRules(t *testing.T, slug string) int {
	t.Helper()
	var n int
	if err := f.st.DB().QueryRow(`SELECT count(*) FROM workspace_rules WHERE slug = ?`, slug).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func (f *fixture) mustGet(t *testing.T, slug string) workspace.Workspace {
	t.Helper()
	w, err := f.svc.Get(context.Background(), slug)
	if err != nil {
		t.Fatalf("Get(%s): %v", slug, err)
	}
	return w
}

// hubRequests is the number of requests the fake hub saw.
func (f *fixture) hubRequests() int { return len(f.hub.Requests()) }

// slugsOf is the slugs of a list's answer, in its order.
func slugsOf(t *testing.T, r answer) []string {
	t.Helper()
	list, ok := r.body["workspaces"].([]any)
	if !ok {
		t.Fatalf("the answer %s has no list of workspaces", r.raw)
	}
	slugs := []string{}
	for _, w := range list {
		slugs = append(slugs, w.(map[string]any)["slug"].(string))
	}
	return slugs
}

// listed is the object of slug in a list's answer.
func listed(t *testing.T, r answer, slug string) map[string]any {
	t.Helper()
	list, _ := r.body["workspaces"].([]any)
	for _, w := range list {
		if obj := w.(map[string]any); obj["slug"] == slug {
			return obj
		}
	}
	t.Fatalf("the list %s does not hold %s", r.raw, slug)
	return nil
}

func TestShow_AnswersTheWorkspaceWithItsSensitiveRules(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	f.hub.SetPolicy("demo", hubtest.Policy{Revision: 3, Default: complete(), Rules: []hubtest.Rule{
		{Name: "nightshift-1", Paths: []string{"deploy/**"},
			Require: hubtest.Requirement{Approvals: 1, Approvers: []string{"owner"}, Checks: slices.Clone(policy.Checks), FreshChecks: true}},
		{Name: "docs", Paths: []string{"docs/**"}, Require: complete()},
		{Name: "secrets", Paths: []string{"deploy/secrets/**"}, Require: hubtest.Requirement{Block: true}},
	}})
	f.insert(t, "demo")

	r := f.do(context.Background(), http.MethodGet, "/api/v1/workspaces/demo", "")

	if r.status != http.StatusOK {
		t.Fatalf("answer = %d %s, want 200", r.status, r.raw)
	}
	want := map[string]any{
		"slug": "demo", "state": "green", "health_command": "make check", "health_timeout": "30m0s",
		"daily_tokens": float64(50000000), "tokens_used_today": float64(0), "created_at": "2026-11-28T10:00:00Z",
		"sensitive_rules": []any{
			map[string]any{"name": "nightshift-1", "paths": []any{"deploy/**"}, "approvals": float64(1), "block": false},
			map[string]any{"name": "secrets", "paths": []any{"deploy/secrets/**"}, "approvals": float64(0), "block": true},
		},
	}
	if !reflect.DeepEqual(r.body, want) {
		t.Errorf("answer = %v, want %v", r.body, want)
	}

	// The rules are the hub's at the moment of the request.
	f.hub.ReplacePolicy("demo", hubtest.Policy{})
	r = f.do(context.Background(), http.MethodGet, "/api/v1/workspaces/demo", "")

	if rules, ok := r.body["sensitive_rules"].([]any); r.status != http.StatusOK || !ok || len(rules) != 0 {
		t.Errorf("after the policy was emptied, answer = %d %s, want 200 with sensitive_rules []", r.status, r.raw)
	}
}

func TestSet_StoresOnlyTheGivenSettings(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		body string
		edit func(*workspace.Workspace)
	}{
		{"health_command only", `{"health_command": "go test ./..."}`, func(w *workspace.Workspace) {
			w.HealthCommand = "go test ./..."
		}},
		{"health_timeout only", `{"health_timeout": "45s"}`, func(w *workspace.Workspace) {
			w.HealthTimeout = 45 * time.Second
		}},
		{"daily_tokens only", `{"daily_tokens": 9}`, func(w *workspace.Workspace) {
			w.DailyTokens = 9
		}},
		{"all three", `{"health_command": "true", "health_timeout": "2h", "daily_tokens": 1000}`, func(w *workspace.Workspace) {
			w.HealthCommand, w.HealthTimeout, w.DailyTokens = "true", 2*time.Hour, 1000
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t, nil)
			f.insert(t, "demo")
			want := inserted("demo")
			tt.edit(&want)

			r := f.do(context.Background(), http.MethodPatch, "/api/v1/workspaces/demo", tt.body)

			if r.status != http.StatusOK {
				t.Fatalf("answer = %d %s, want 200", r.status, r.raw)
			}
			members := map[string]any{
				"slug": "demo", "state": "green", "health_command": want.HealthCommand,
				"health_timeout": want.HealthTimeout.String(), "daily_tokens": float64(want.DailyTokens),
				"tokens_used_today": float64(0), "created_at": "2026-11-28T10:00:00Z",
			}
			if !reflect.DeepEqual(r.body, members) {
				t.Errorf("answer = %v, want %v", r.body, members)
			}
			if got := f.mustGet(t, "demo"); got != want {
				t.Errorf("Get = %+v, want %+v", got, want)
			}
			if n := f.hubRequests(); n != 0 {
				t.Errorf("the hub saw %d requests, want none", n)
			}
		})
	}
}

func TestSet_RejectsAnInvalidBody(t *testing.T) {
	t.Parallel()
	tests := []struct{ name, body string }{
		{"no member", `{}`},
		{"unknown member", `{"state": "red"}`},
		{"unknown member beside a known one", `{"daily_tokens": 9, "state": "red"}`},
		{"empty health_command", `{"health_command": ""}`},
		{"zero health_timeout", `{"health_timeout": "0s"}`},
		{"health_timeout not a duration", `{"health_timeout": "soon"}`},
		{"zero daily_tokens", `{"daily_tokens": 0}`},
		{"fractional daily_tokens", `{"daily_tokens": 1.5}`},
		{"not an object", `[]`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t, nil)
			f.insert(t, "demo")

			r := f.do(context.Background(), http.MethodPatch, "/api/v1/workspaces/demo", tt.body)

			if r.status != http.StatusBadRequest || r.body["error_type"] != "invalid_request" {
				t.Fatalf("answer = %d %s, want 400 invalid_request", r.status, r.raw)
			}
			if msg, _ := r.body["message"].(string); msg == "" {
				t.Error("the answer has no message")
			}
			if got := f.mustGet(t, "demo"); got != inserted("demo") {
				t.Errorf("Get = %+v, want it unchanged: %+v", got, inserted("demo"))
			}
		})
	}
}

func TestList_ReportsEveryWorkspaceInSlugOrder(t *testing.T) {
	t.Parallel()
	t.Run("two workspaces", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t, nil)
		f.insert(t, "zeta")
		f.insert(t, "alpha")

		r := f.do(context.Background(), http.MethodGet, "/api/v1/workspaces", "")

		if r.status != http.StatusOK || !slices.Equal(slugsOf(t, r), []string{"alpha", "zeta"}) {
			t.Fatalf("answer = %d %s, want 200 with alpha, then zeta", r.status, r.raw)
		}
		for _, slug := range []string{"alpha", "zeta"} {
			if got := listed(t, r, slug)["tokens_used_today"]; got != float64(0) {
				t.Errorf("tokens_used_today of %s = %v without a source, want 0", slug, got)
			}
		}

		f.svc.SetTokensUsed(func(slug string) int64 {
			if slug == "zeta" {
				return 42
			}
			return 0
		})
		f.svc.Decorate(func(slug string, obj map[string]any) { obj["paused"] = slug == "alpha" })
		r = f.do(context.Background(), http.MethodGet, "/api/v1/workspaces", "")

		if got := listed(t, r, "zeta")["tokens_used_today"]; got != float64(42) {
			t.Errorf("tokens_used_today of zeta = %v, want 42 from the source", got)
		}
		if got := listed(t, r, "alpha")["paused"]; got != true {
			t.Errorf("paused of alpha = %v, want true from the decoration", got)
		}
		if got := listed(t, r, "zeta")["paused"]; got != false {
			t.Errorf("paused of zeta = %v, want false from the decoration", got)
		}
		s := f.do(context.Background(), http.MethodGet, "/api/v1/status", "")
		want := []any{
			map[string]any{"slug": "alpha", "state": "green", "tokens_used_today": float64(0)},
			map[string]any{"slug": "zeta", "state": "green", "tokens_used_today": float64(42)},
		}
		if s.status != http.StatusOK || !reflect.DeepEqual(s.body["workspaces"], want) {
			t.Errorf("status = %d, workspaces %v, want %v", s.status, s.body["workspaces"], want)
		}
		if n := f.hubRequests(); n != 0 {
			t.Errorf("the hub saw %d requests, want none", n)
		}
	})
	t.Run("none", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t, nil)

		r := f.do(context.Background(), http.MethodGet, "/api/v1/workspaces", "")
		s := f.do(context.Background(), http.MethodGet, "/api/v1/status", "")

		if list, ok := r.body["workspaces"].([]any); r.status != http.StatusOK || !ok || len(list) != 0 {
			t.Errorf("list = %d %s, want 200 with workspaces []", r.status, r.raw)
		}
		if list, ok := s.body["workspaces"].([]any); s.status != http.StatusOK || !ok || len(list) != 0 {
			t.Errorf("status = %d %s, want 200 with workspaces []", s.status, s.raw)
		}
		if n := f.hubRequests(); n != 0 {
			t.Errorf("the hub saw %d requests, want none", n)
		}
	})
}

func TestSetState_StoresTheStateAcrossARestart(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name   string
		states []workspace.State
	}{
		{"red", []workspace.State{workspace.Red}},
		{"held", []workspace.State{workspace.Held}},
		{"green after red", []workspace.State{workspace.Red, workspace.Green}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t, nil)
			f.insert(t, "demo")
			want := inserted("demo")
			want.State = tt.states[len(tt.states)-1]

			for _, state := range tt.states {
				if err := f.svc.SetState(context.Background(), "demo", state); err != nil {
					t.Fatalf("SetState(%s): %v", state, err)
				}
			}

			if got := f.mustGet(t, "demo"); got.State != want.State {
				t.Errorf("Get().State = %q, want %q", got.State, want.State)
			}
			r := f.do(context.Background(), http.MethodGet, "/api/v1/workspaces", "")
			if got := listed(t, r, "demo")["state"]; got != string(want.State) {
				t.Errorf("the list's state = %v, want %s", got, want.State)
			}
			if err := f.st.Close(); err != nil {
				t.Fatal(err)
			}
			f.open(t)
			if got := f.mustGet(t, "demo"); got != want {
				t.Errorf("after the store was opened again, Get = %+v, want %+v", got, want)
			}
		})
	}
}

func TestSetState_RefusesAnUnknownState(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	f.insert(t, "demo")

	err := f.svc.SetState(context.Background(), "demo", "blue")

	if err == nil || !strings.Contains(err.Error(), "blue") {
		t.Errorf("SetState(blue) = %v, want an error naming the state", err)
	}
	if got := f.mustGet(t, "demo"); got.State != workspace.Green {
		t.Errorf("Get().State = %q, want green unchanged", got.State)
	}
}

func TestUnregisteredSlug_IsNotFoundWithoutAskingTheHub(t *testing.T) {
	t.Parallel()
	t.Run("functions", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t, nil)
		ctx := context.Background()
		tokens := int64(9)
		calls := map[string]func() error{
			"SetState": func() error { return f.svc.SetState(ctx, "nope", workspace.Red) },
			"Set": func() error {
				_, err := f.svc.Set(ctx, "nope", workspace.Settings{DailyTokens: &tokens})
				return err
			},
			"Remove": func() error { return f.svc.Remove(ctx, "nope") },
			"Protect": func() error {
				_, err := f.svc.Protect(ctx, "nope", []string{"x/**"}, false, "")
				return err
			},
			"Unprotect": func() error {
				_, err := f.svc.Unprotect(ctx, "nope", "x")
				return err
			},
		}
		for name, call := range calls {
			if err := call(); !errors.Is(err, workspace.ErrNotFound) {
				t.Errorf("%s(nope) = %v, want ErrNotFound", name, err)
			}
		}
		if n := f.hubRequests(); n != 0 {
			t.Errorf("the hub saw %d requests, want none", n)
		}
	})
	routes := []struct{ method, path, body string }{
		{http.MethodGet, "/api/v1/workspaces/nope", ""},
		{http.MethodPatch, "/api/v1/workspaces/nope", `{"daily_tokens": 9}`},
		{http.MethodDelete, "/api/v1/workspaces/nope", ""},
		{http.MethodPost, "/api/v1/workspaces/nope/policy/rules", `{"globs": ["x/**"]}`},
		{http.MethodDelete, "/api/v1/workspaces/nope/policy/rules/x", ""},
	}
	for _, tt := range routes {
		t.Run(tt.method+" "+tt.path, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t, nil)
			f.insert(t, "demo")

			r := f.do(context.Background(), tt.method, tt.path, tt.body)

			if r.status != http.StatusNotFound || r.body["error_type"] != "not_found" {
				t.Fatalf("answer = %d %s, want 404 not_found", r.status, r.raw)
			}
			if want := "workspace nope is not registered"; r.body["message"] != want {
				t.Errorf("message = %q, want %q", r.body["message"], want)
			}
			if n := f.hubRequests(); n != 0 {
				t.Errorf("the hub saw %d requests, want none", n)
			}
		})
	}
}

// hookLog records the calls of removal hooks and guards.
type hookLog struct {
	mu    sync.Mutex
	calls []string
}

func (l *hookLog) add(call string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.calls = append(l.calls, call)
}

func (l *hookLog) list() []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return slices.Clone(l.calls)
}

func TestRemove_RemovesTheWorkspaceItsRulesAndItsClone(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	f.hub.SetPolicy("demo", hubtest.Policy{Revision: 4, Default: complete(), Rules: []hubtest.Rule{
		{Name: "nightshift-1", Paths: []string{"deploy/**"},
			Require: hubtest.Requirement{Approvals: 1, Approvers: []string{"owner"}, Checks: slices.Clone(policy.Checks), FreshChecks: true}},
	}})
	if r := f.register(t, map[string]any{"slug": "demo", "health_command": "true"}); r.status != http.StatusCreated {
		t.Fatalf("registration answered %d %s, want 201", r.status, r.raw)
	}
	f.recordRule(t, "demo", "nightshift-1")
	clone := filepath.Join(f.clones, "demo")
	if _, err := os.Stat(filepath.Join(clone, "README.md")); err != nil {
		t.Fatalf("the clone of demo: %v", err)
	}
	var hooks, guards hookLog
	f.svc.AddRemoveGuard(func(_ context.Context, slug string) error { guards.add(slug); return nil })
	f.svc.OnRemoved(func(_ context.Context, slug string) { hooks.add("first:" + slug) })
	f.svc.OnRemoved(func(_ context.Context, slug string) { hooks.add("second:" + slug) })
	policyBefore := f.hub.Policy("demo")
	requests := f.hubRequests()

	r := f.do(context.Background(), http.MethodDelete, "/api/v1/workspaces/demo", "")

	if r.status != http.StatusNoContent {
		t.Fatalf("answer = %d %s, want 204", r.status, r.raw)
	}
	f.assertNoneStored(t)
	if n := f.recordedRules(t, "demo"); n != 0 {
		t.Errorf("%d rule names of demo are still recorded, want none", n)
	}
	if _, err := os.Stat(clone); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("the clone directory is left (stat error %v), want it removed", err)
	}
	if got := guards.list(); !slices.Equal(got, []string{"demo"}) {
		t.Errorf("the guard was asked %v, want once for demo", got)
	}
	if got := hooks.list(); !slices.Equal(got, []string{"first:demo", "second:demo"}) {
		t.Errorf("the hooks ran as %v, want first:demo, then second:demo", got)
	}
	if n := f.hubRequests() - requests; n != 0 {
		t.Errorf("the removal sent %d requests to the hub, want none", n)
	}
	if p := f.hub.Policy("demo"); !reflect.DeepEqual(p, policyBefore) {
		t.Errorf("the hub's policy = %+v, want it unchanged: %+v", p, policyBefore)
	}
	if r := f.register(t, map[string]any{"slug": "demo", "health_command": "true"}); r.status != http.StatusCreated {
		t.Errorf("a new registration of demo answered %d %s, want 201", r.status, r.raw)
	}
}

func TestRemove_IsRefusedByTheFirstGuard(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	f.insert(t, "demo")
	f.recordRule(t, "demo", "nightshift-1")
	clone := filepath.Join(f.clones, "demo")
	if err := os.MkdirAll(clone, 0o755); err != nil {
		t.Fatal(err)
	}
	var calls hookLog
	f.svc.AddRemoveGuard(func(_ context.Context, slug string) error {
		calls.add("first guard:" + slug)
		return errors.New(slug + " has open work items: 4, 7")
	})
	f.svc.AddRemoveGuard(func(_ context.Context, slug string) error {
		calls.add("second guard:" + slug)
		return nil
	})
	f.svc.OnRemoved(func(_ context.Context, slug string) { calls.add("hook:" + slug) })

	r := f.do(context.Background(), http.MethodDelete, "/api/v1/workspaces/demo", "")

	if r.status != http.StatusConflict || r.body["error_type"] != "conflict" {
		t.Fatalf("answer = %d %s, want 409 conflict", r.status, r.raw)
	}
	if want := "demo has open work items: 4, 7"; r.body["message"] != want {
		t.Errorf("message = %q, want %q", r.body["message"], want)
	}
	if got := calls.list(); !slices.Equal(got, []string{"first guard:demo"}) {
		t.Errorf("the calls were %v, want only the first guard's", got)
	}
	if got := f.mustGet(t, "demo"); got != inserted("demo") {
		t.Errorf("Get = %+v, want demo unchanged", got)
	}
	if n := f.recordedRules(t, "demo"); n != 1 {
		t.Errorf("%d rule names of demo are recorded, want the one kept", n)
	}
	if _, err := os.Stat(clone); err != nil {
		t.Errorf("the clone directory: %v, want it kept", err)
	}
}
