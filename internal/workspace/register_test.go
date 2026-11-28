package workspace_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/api"
	"github.com/agent-fox-dev/nightshift/internal/command"
	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
	"github.com/agent-fox-dev/nightshift/internal/policy"
	"github.com/agent-fox-dev/nightshift/internal/store"
	"github.com/agent-fox-dev/nightshift/internal/store/storetest"
	"github.com/agent-fox-dev/nightshift/internal/workspace"
)

const clientToken = "client-token"

const noSensitivePath = "no path of demo is marked sensitive; every change lands without you"

// The requests of a registration as hubLog names them.
const (
	getWorkspace = "GET /api/v1/workspaces/demo"
	postSync     = "POST /api/v1/workspaces/demo/sync"
	getPolicy    = "GET /api/v1/workspaces/demo/policy"
	gitFetch     = "git fetch"
)

// complete is a requirement that counts nightshift's three checks on the
// version itself.
func complete() hubtest.Requirement {
	return hubtest.Requirement{Checks: slices.Clone(policy.Checks), FreshChecks: true}
}

// fixture is a fake hub holding the workspace demo and a workspace service
// over a store copied from the template, mounted on an API server.
type fixture struct {
	hub              *hubtest.Hub
	repo             *hubtest.Repository
	worker, approver string
	// st is the store at path, which the service reads and writes.
	st     *store.Store
	path   string
	svc    *workspace.Service
	srv    *api.Server
	clones string
}

// newFixture gives the fake hub the workspace demo, owned by the worker
// token's user, with a repository, sync_enabled on, a sync that reports
// main up_to_date and a policy whose default requires the three checks.
// edit, when not nil, changes the workspace before the hub gets it.
func newFixture(t *testing.T, edit func(*hubtest.Workspace)) *fixture {
	t.Helper()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	ws := hubtest.Workspace{Slug: "demo"}
	if edit != nil {
		edit(&ws)
	}
	repo := h.AddWorkspace(t, ws)
	h.SetPolicy("demo", hubtest.Policy{Default: complete()})
	f := &fixture{hub: h, repo: repo, worker: worker, approver: approver,
		path: storetest.Path(t), clones: filepath.Join(t.TempDir(), "clones")}
	f.open(t)
	return f
}

// open opens the fixture's store and builds the service over it, mounted
// on a new API server, as a start of the daemon does.
func (f *fixture) open(t *testing.T) {
	t.Helper()
	st, err := store.Open(f.path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	f.st = st
	f.svc = workspace.New(st, hub.New(f.hub.URL(), f.worker, f.approver), &command.Runner{}, f.clones)
	f.srv = api.New(clientToken, api.HubReporterFunc(func() api.HubState { return api.HubState{} }))
	f.svc.Mount(f.srv)
}

// answer is the API's answer: its status and its JSON body.
type answer struct {
	status int
	body   map[string]any
	raw    string
}

func (f *fixture) do(ctx context.Context, method, path, body string) answer {
	req := httptest.NewRequestWithContext(ctx, method, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+clientToken)
	rec := httptest.NewRecorder()
	f.srv.ServeHTTP(rec, req)
	var m map[string]any
	json.Unmarshal(rec.Body.Bytes(), &m)
	return answer{status: rec.Code, body: m, raw: rec.Body.String()}
}

// register posts a registration with the members of body.
func (f *fixture) register(t *testing.T, body map[string]any) answer {
	t.Helper()
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	return f.do(context.Background(), http.MethodPost, "/api/v1/workspaces", string(data))
}

// hubLog is the hub's requests as "<method> <path>", with a run of git
// requests, which one fetch is, as one gitFetch.
func hubLog(h *hubtest.Hub) []string {
	var out []string
	for _, r := range h.Requests() {
		if strings.HasPrefix(r.Path, "/git/") {
			if len(out) == 0 || out[len(out)-1] != gitFetch {
				out = append(out, gitFetch)
			}
			continue
		}
		out = append(out, r.Method+" "+r.Path)
	}
	return out
}

func (f *fixture) assertNoneStored(t *testing.T) {
	t.Helper()
	list, err := f.svc.List(context.Background())
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(list) != 0 {
		t.Errorf("List = %+v, want no workspace stored", list)
	}
}

func (f *fixture) assertRefused(t *testing.T, r answer, message string) {
	t.Helper()
	if r.status != http.StatusConflict || r.body["error_type"] != "conflict" {
		t.Fatalf("answer = %d %s, want 409 conflict", r.status, r.raw)
	}
	if r.body["message"] != message {
		t.Errorf("message = %q, want %q", r.body["message"], message)
	}
}

// gate is a health command that marks when it runs and then waits until
// the test releases it.
type gate struct{ line, started, release string }

func newGate(t *testing.T) gate {
	dir := t.TempDir()
	g := gate{started: filepath.Join(dir, "started"), release: filepath.Join(dir, "release")}
	g.line = fmt.Sprintf(`touch '%s'; while [ ! -f '%s' ]; do sleep 0.01; done`, g.started, g.release)
	return g
}

func (g gate) awaitStart(t *testing.T) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := os.Stat(g.started); err == nil {
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("the health command did not start within 10s")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func (g gate) open(t *testing.T) {
	t.Helper()
	if err := os.WriteFile(g.release, nil, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestRegister_RunsTheChecksInOrderAndStoresTheWorkspace(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	// The sync reports the first commit, not the tip, so that the clone is
	// seen to be at the commit the sync reported.
	first := f.repo.Commits(t)[0]
	f.hub.UpdateWorkspace("demo", func(w *hubtest.Workspace) {
		w.Branches = []hubtest.SyncBranch{{Name: "main", Result: "up_to_date", LocalSHA: first}}
	})
	out := filepath.Join(t.TempDir(), "out")
	health := `test -f README.md && git rev-parse HEAD >> '` + out + `'`

	r := f.register(t, map[string]any{"slug": "demo", "health_command": health})

	if r.status != http.StatusCreated {
		t.Fatalf("answer = %d %s, want 201", r.status, r.raw)
	}
	want := map[string]any{
		"slug": "demo", "state": "green", "health_command": health, "health_timeout": "30m0s",
		"daily_tokens": float64(50000000), "tokens_used_today": float64(0),
		"policy_revision": nil, "warning": noSensitivePath,
	}
	for k, v := range want {
		if got, ok := r.body[k]; !ok || !reflect.DeepEqual(got, v) {
			t.Errorf("%s = %#v (present %v), want %#v", k, got, ok, v)
		}
	}
	if s, _ := r.body["created_at"].(string); !strings.HasSuffix(s, "Z") {
		t.Errorf("created_at = %#v, want an RFC 3339 time in UTC", r.body["created_at"])
	} else if _, err := time.Parse(time.RFC3339, s); err != nil {
		t.Errorf("created_at = %q: %v", s, err)
	}
	if got, want := hubLog(f.hub), []string{getWorkspace, postSync, getPolicy, gitFetch}; !slices.Equal(got, want) {
		t.Errorf("the hub saw %q, want %q", got, want)
	}
	for _, req := range f.hub.Requests() {
		cred := req.Bearer
		if strings.HasPrefix(req.Path, "/git/") {
			cred = req.Basic
		}
		if cred != f.worker {
			t.Errorf("%s %s carried %q, want the worker token", req.Method, req.Path, cred)
		}
	}
	ran, err := os.ReadFile(out)
	if err != nil {
		t.Fatalf("the health command did not run: %v", err)
	}
	if string(ran) != first+"\n" {
		t.Errorf("the health command ran at %q, want once at %s", ran, first)
	}
	stored, err := f.svc.Get(context.Background(), "demo")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if stored.Slug != "demo" || stored.State != workspace.Green || stored.HealthCommand != health ||
		stored.HealthTimeout != 30*time.Minute || stored.DailyTokens != 50000000 || stored.GitURL != f.repo.URL() {
		t.Errorf("Get = %+v, want demo stored green with the registration's values and the hub's git URL", stored)
	}
}

func TestRegister_RefusesBeforeTheHealthCommand(t *testing.T) {
	t.Parallel()
	branch := func(name, result string) func(*hubtest.Workspace) {
		return func(w *hubtest.Workspace) { w.Branches = []hubtest.SyncBranch{{Name: name, Result: result}} }
	}
	lacking := &hubtest.Policy{Rules: []hubtest.Rule{
		{Name: "docs", Paths: []string{"docs/**"}},
		{Name: "secrets", Paths: []string{"secrets/**"}, Require: hubtest.Requirement{Block: true}},
	}}
	tests := []struct {
		name      string
		workspace func(*hubtest.Workspace)
		policy    *hubtest.Policy
		want      string
		// decidedBy is the last request the hub saw.
		decidedBy string
	}{
		{"no such workspace", func(w *hubtest.Workspace) { w.Slug = "other" }, nil, "workspace not found", getWorkspace},
		{"access member", func(w *hubtest.Workspace) {
			w.Owner, w.Access = "u_other", map[string]string{hubtest.Owner: "member"}
		}, nil, "not the owner of demo", getWorkspace},
		{"access admin", func(w *hubtest.Workspace) {
			w.Owner, w.Access = "u_other", map[string]string{hubtest.Owner: "admin"}
		}, nil, "not the owner of demo", getWorkspace},
		{"sync disabled", func(w *hubtest.Workspace) { w.SyncDisabled = true }, nil, "sync is disabled for demo", getWorkspace},
		{"default branch ahead", branch("main", "ahead"), nil, "default branch is ahead of origin", postSync},
		{"default branch diverged", branch("main", "diverged"), nil, "default branch has diverged from origin", postSync},
		{"default branch reset", branch("main", "reset"), nil, "default branch: sync reported reset", postSync},
		{"default branch name conflict", branch("main", "name_conflict"), nil, "default branch: sync reported name_conflict", postSync},
		{"default branch not reported", branch("dev", "up_to_date"), nil, "default branch main is not on origin", postSync},
		{"workspace busy", func(w *hubtest.Workspace) {
			w.SyncError = &hubtest.ErrorAnswer{Status: 409, Name: "workspace_busy", Message: "the workspace is busy"}
		}, nil, "hub answered 409 workspace_busy: the workspace is busy", postSync},
		{"task fork", func(w *hubtest.Workspace) {
			w.Kind = "fork"
			w.SyncError = &hubtest.ErrorAnswer{Status: 400, Name: "fork_has_no_origin", Message: "a task fork has no origin"}
		}, nil, "hub answered 400 fork_has_no_origin: a task fork has no origin", postSync},
		{"policy lacking the checks", nil, lacking,
			"policy of demo does not require nightshift's checks in: default, docs; run again with --adopt-policy", getPolicy},
		{"no git URL", func(w *hubtest.Workspace) { w.NoGitURL = true }, nil, "cannot clone demo: the hub reports no git URL", getPolicy},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t, tt.workspace)
			if tt.policy != nil {
				f.hub.SetPolicy("demo", *tt.policy)
			}
			revision := f.hub.Policy("demo").Revision

			r := f.register(t, map[string]any{"slug": "demo", "health_command": "true"})

			f.assertRefused(t, r, tt.want)
			f.assertNoneStored(t)
			log := hubLog(f.hub)
			if len(log) == 0 || log[len(log)-1] != tt.decidedBy {
				t.Errorf("the hub saw %q, want nothing after %q", log, tt.decidedBy)
			}
			if got := f.hub.Policy("demo").Revision; got != revision {
				t.Errorf("the policy's revision = %d, want %d unchanged", got, revision)
			}
			if _, err := os.Stat(filepath.Join(f.clones, "demo")); !errors.Is(err, os.ErrNotExist) {
				t.Errorf("the clone directory exists (stat error %v), want none", err)
			}
		})
	}
}

func TestRegister_RefusesAFailingHealthCommand(t *testing.T) {
	t.Parallel()
	var sixty []any
	for i := 11; i <= 60; i++ {
		sixty = append(sixty, strconv.Itoa(i))
	}
	tests := []struct {
		name    string
		command string
		timeout string
		// gitAnswer is the status the hub's git endpoint answers; 0 serves
		// git.
		gitAnswer int
		want      string
		// prefixOnly accepts any text after want.
		prefixOnly bool
		output     []any
	}{
		{name: "exits 3", command: "seq 1 60; exit 3", want: "health command exited 3", output: sixty},
		{name: "times out", command: "echo started; sleep 60", timeout: "100ms", want: "health command timed out", output: []any{"started"}},
		{name: "clone fails", command: "true", gitAnswer: http.StatusInternalServerError, want: "cannot clone demo: ", prefixOnly: true},
	}
	for _, tt := range tests {
		for _, adopt := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s adopt %v", tt.name, adopt), func(t *testing.T) {
				t.Parallel()
				f := newFixture(t, nil)
				if adopt {
					f.hub.SetPolicy("demo", hubtest.Policy{})
				}
				if tt.gitAnswer != 0 {
					f.repo.SetAnswer(tt.gitAnswer)
				}
				body := map[string]any{"slug": "demo", "health_command": tt.command, "adopt_policy": adopt}
				if tt.timeout != "" {
					body["health_timeout"] = tt.timeout
				}

				start := time.Now()
				r := f.register(t, body)
				elapsed := time.Since(start)

				if r.status != http.StatusConflict || r.body["error_type"] != "conflict" {
					t.Fatalf("answer = %d %s, want 409 conflict", r.status, r.raw)
				}
				msg, _ := r.body["message"].(string)
				if tt.prefixOnly && (!strings.HasPrefix(msg, tt.want) || msg == tt.want) || !tt.prefixOnly && msg != tt.want {
					t.Errorf("message = %q, want %q", msg, tt.want)
				}
				if got, ok := r.body["output"]; tt.output != nil && !reflect.DeepEqual(got, tt.output) || tt.output == nil && ok {
					t.Errorf("output = %#v, want %#v", got, tt.output)
				}
				if elapsed > 5*time.Second {
					t.Errorf("the answer took %v, want it within 5s", elapsed)
				}
				f.assertNoneStored(t)
				if puts := f.hub.PolicyPuts("demo"); len(puts) != 0 {
					t.Errorf("the hub saw %d PUTs of the policy, want none", len(puts))
				}
				if _, err := os.Stat(filepath.Join(f.clones, "demo")); !errors.Is(err, os.ErrNotExist) {
					t.Errorf("the clone directory is left (stat error %v), want it removed", err)
				}
			})
		}
	}
}

func TestRegister_RejectsAnInvalidBody(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name, body string
		// member is what the message names; empty for a body that is no
		// object.
		member string
	}{
		{"not an object", `[]`, ""},
		{"empty slug", `{"slug": "", "health_command": "true"}`, "slug"},
		// The slug names the clone's directory, so it is one path element.
		{"slug of the clones directory", `{"slug": ".", "health_command": "true"}`, "slug"},
		{"slug of its parent", `{"slug": "..", "health_command": "true"}`, "slug"},
		{"slug holding a slash", `{"slug": "a/b", "health_command": "true"}`, "slug"},
		{"slug holding a backslash", `{"slug": "a\\b", "health_command": "true"}`, "slug"},
		{"no health command", `{"slug": "demo"}`, "health_command"},
		{"timeout not a duration", `{"slug": "demo", "health_command": "true", "health_timeout": "soon"}`, "health_timeout"},
		{"timeout zero", `{"slug": "demo", "health_command": "true", "health_timeout": "0s"}`, "health_timeout"},
		{"timeout negative", `{"slug": "demo", "health_command": "true", "health_timeout": "-1m"}`, "health_timeout"},
		{"daily tokens zero", `{"slug": "demo", "health_command": "true", "daily_tokens": 0}`, "daily_tokens"},
		{"daily tokens negative", `{"slug": "demo", "health_command": "true", "daily_tokens": -5}`, "daily_tokens"},
		{"daily tokens not a number", `{"slug": "demo", "health_command": "true", "daily_tokens": "many"}`, "daily_tokens"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t, nil)

			r := f.do(context.Background(), http.MethodPost, "/api/v1/workspaces", tt.body)

			if r.status != http.StatusBadRequest || r.body["error_type"] != "invalid_request" {
				t.Fatalf("answer = %d %s, want 400 invalid_request", r.status, r.raw)
			}
			if msg, _ := r.body["message"].(string); msg == "" || !strings.Contains(msg, tt.member) {
				t.Errorf("message = %q, want one naming %q", msg, tt.member)
			}
			if n := len(f.hub.Requests()); n != 0 {
				t.Errorf("the hub saw %d requests, want none", n)
			}
			f.assertNoneStored(t)
		})
	}
}

func TestRegister_RefusesASlugInProgressOrRegistered(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	g := newGate(t)
	first := make(chan answer, 1)
	go func() {
		first <- f.do(context.Background(), http.MethodPost, "/api/v1/workspaces",
			`{"slug": "demo", "health_command": `+strconv.Quote(g.line)+`}`)
	}()
	g.awaitStart(t)

	seen := len(f.hub.Requests())
	r2 := f.register(t, map[string]any{"slug": "demo", "health_command": "true"})
	f.assertRefused(t, r2, "registration of demo is in progress")
	if n := len(f.hub.Requests()); n != seen {
		t.Errorf("the hub saw %d more requests for the second registration, want none", n-seen)
	}

	g.open(t)
	select {
	case r1 := <-first:
		if r1.status != http.StatusCreated {
			t.Fatalf("the first registration answered %d %s, want 201", r1.status, r1.raw)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the first registration did not answer within 10s")
	}

	seen = len(f.hub.Requests())
	r3 := f.register(t, map[string]any{"slug": "demo", "health_command": "echo other"})
	f.assertRefused(t, r3, "demo is registered already")
	if n := len(f.hub.Requests()); n != seen {
		t.Errorf("the hub saw %d more requests for the third registration, want none", n-seen)
	}
	stored, err := f.svc.Get(context.Background(), "demo")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if stored.HealthCommand != g.line {
		t.Errorf("the stored health command = %q, want the first registration's %q", stored.HealthCommand, g.line)
	}
}

func TestRegister_StoresTheDefaultsAtRegistration(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		body map[string]any
		// defaultTokens, when not 0, is the service's DefaultDailyTokens
		// at the registration.
		defaultTokens int64
		wantTimeout   string
		wantTokens    int64
	}{
		{"both omitted", nil, 0, "30m0s", 50000000},
		{"another default set before", nil, 7, "30m0s", 7},
		{"both given", map[string]any{"health_timeout": "90s", "daily_tokens": 12}, 0, "1m30s", 12},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t, nil)
			if tt.defaultTokens != 0 {
				f.svc.DefaultDailyTokens = tt.defaultTokens
			}
			body := map[string]any{"slug": "demo", "health_command": "true"}
			for k, v := range tt.body {
				body[k] = v
			}

			r := f.register(t, body)

			if r.status != http.StatusCreated {
				t.Fatalf("answer = %d %s, want 201", r.status, r.raw)
			}
			if r.body["health_timeout"] != tt.wantTimeout || r.body["daily_tokens"] != float64(tt.wantTokens) {
				t.Errorf("health_timeout, daily_tokens = %v, %v; want %s, %d",
					r.body["health_timeout"], r.body["daily_tokens"], tt.wantTimeout, tt.wantTokens)
			}
			f.svc.DefaultDailyTokens = 9
			stored, err := f.svc.Get(context.Background(), "demo")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if stored.DailyTokens != tt.wantTokens || stored.HealthTimeout.String() != tt.wantTimeout {
				t.Errorf("Get = %+v, want %s and %d as stored at the registration", stored, tt.wantTimeout, tt.wantTokens)
			}
		})
	}
}

// documentOf is the fake hub's policy as the policy package reads it.
func documentOf(t *testing.T, p hubtest.Policy) policy.Document {
	t.Helper()
	data, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	var doc policy.Document
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func TestRegister_AdoptsThePolicyAfterTheHealthCommand(t *testing.T) {
	t.Parallel()
	t.Run("a policy lacking the checks", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t, nil)
		f.hub.SetPolicy("demo", hubtest.Policy{Revision: 2, Rules: []hubtest.Rule{
			{Name: "docs", Paths: []string{"docs/**"}, Exclude: []string{"docs/drafts/**"}, Targets: []string{"main"},
				Require: hubtest.Requirement{Approvers: []string{"member"}, Checks: []string{"spell"}}},
			{Name: "secrets", Paths: []string{"secrets/**"}, Require: hubtest.Requirement{Block: true}},
		}})
		before := f.hub.Policy("demo")

		r := f.register(t, map[string]any{"slug": "demo", "health_command": "true", "adopt_policy": true})

		if r.status != http.StatusCreated {
			t.Fatalf("answer = %d %s, want 201", r.status, r.raw)
		}
		if r.body["policy_revision"] != float64(3) {
			t.Errorf("policy_revision = %#v, want 3", r.body["policy_revision"])
		}
		p := f.hub.Policy("demo")
		if !slices.Equal(p.Default.Checks, policy.Checks) || !p.Default.FreshChecks {
			t.Errorf("default = %+v, want the three checks with fresh_checks", p.Default)
		}
		docs := before.Rules[0]
		docs.Require.Checks = append([]string{"spell"}, policy.Checks...)
		docs.Require.FreshChecks = true
		if len(p.Rules) != 2 || !reflect.DeepEqual(p.Rules[0], docs) || !reflect.DeepEqual(p.Rules[1], before.Rules[1]) {
			t.Errorf("rules = %+v, want docs with spell and the three checks, then secrets unchanged: %+v", p.Rules, []hubtest.Rule{docs, before.Rules[1]})
		}
		if puts := f.hub.PolicyPuts("demo"); len(puts) != 1 || strings.Trim(puts[0].IfMatch, `"`) != "2" {
			t.Errorf("the hub saw the PUTs %+v, want one with If-Match 2", puts)
		}
		if missing := policy.Missing(documentOf(t, p)); len(missing) != 0 {
			t.Errorf("Missing of the adopted policy = %q, want none", missing)
		}
		if _, err := f.svc.Get(context.Background(), "demo"); err != nil {
			t.Errorf("Get: %v, want demo stored", err)
		}
	})
	t.Run("a complete policy", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t, nil)

		r := f.register(t, map[string]any{"slug": "demo", "health_command": "true", "adopt_policy": true})

		if r.status != http.StatusCreated {
			t.Fatalf("answer = %d %s, want 201", r.status, r.raw)
		}
		if v, ok := r.body["policy_revision"]; !ok || v != nil {
			t.Errorf("policy_revision = %#v (present %v), want null", v, ok)
		}
		if puts := f.hub.PolicyPuts("demo"); len(puts) != 0 {
			t.Errorf("the hub saw %d PUTs, want none", len(puts))
		}
	})
}

func TestRegister_RefusesWhenThePolicyChangedMeanwhile(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	f.hub.SetPolicy("demo", hubtest.Policy{})
	g := newGate(t)
	result := make(chan answer, 1)
	go func() {
		result <- f.do(context.Background(), http.MethodPost, "/api/v1/workspaces",
			`{"slug": "demo", "adopt_policy": true, "health_command": `+strconv.Quote(g.line)+`}`)
	}()
	g.awaitStart(t)
	f.hub.ReplacePolicy("demo", hubtest.Policy{Rules: []hubtest.Rule{{Name: "docs", Paths: []string{"docs/**"}}}})
	g.open(t)

	var r answer
	select {
	case r = <-result:
	case <-time.After(10 * time.Second):
		t.Fatal("the registration did not answer within 10s")
	}

	f.assertRefused(t, r, "policy changed meanwhile; run the command again")
	if puts := f.hub.PolicyPuts("demo"); len(puts) != 1 {
		t.Errorf("the hub saw %d PUTs, want exactly one", len(puts))
	}
	f.assertNoneStored(t)
}

func TestRegister_WarnsWhenNoPathIsSensitive(t *testing.T) {
	t.Parallel()
	approval := complete()
	approval.Approvals, approval.Approvers = 1, []string{"owner"}
	tests := []struct {
		name   string
		policy hubtest.Policy
		want   any
	}{
		{"complete default only", hubtest.Policy{Default: complete()}, noSensitivePath},
		{"a blocking rule", hubtest.Policy{Default: complete(), Rules: []hubtest.Rule{
			{Name: "secrets", Paths: []string{"secrets/**"}, Require: hubtest.Requirement{Block: true}},
		}}, nil},
		{"a rule with an approval", hubtest.Policy{Default: complete(), Rules: []hubtest.Rule{
			{Name: "deploy", Paths: []string{"deploy/**"}, Require: approval},
		}}, nil},
		{"a default with an approval", hubtest.Policy{Default: approval}, nil},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t, nil)
			f.hub.SetPolicy("demo", tt.policy)

			r := f.register(t, map[string]any{"slug": "demo", "health_command": "true"})

			if r.status != http.StatusCreated {
				t.Fatalf("answer = %d %s, want 201", r.status, r.raw)
			}
			if got, ok := r.body["warning"]; !ok || got != tt.want {
				t.Errorf("warning = %#v (present %v), want %#v", got, ok, tt.want)
			}
		})
	}
}

// silenceHub builds the fixture's service again over a hub that takes
// connections and never answers, with a short HubTimeout.
func (f *fixture) silenceHub(t *testing.T) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			t.Cleanup(func() { conn.Close() })
		}
	}()
	f.svc = workspace.New(f.st, hub.New("http://"+ln.Addr().String(), f.worker, f.approver), &command.Runner{}, f.clones)
	f.svc.HubTimeout = 100 * time.Millisecond
	f.srv = api.New(clientToken, api.HubReporterFunc(func() api.HubState { return api.HubState{} }))
	f.svc.Mount(f.srv)
}

func TestRoutes_AnswerUnavailableWhileTheHubIsDown(t *testing.T) {
	t.Parallel()
	hubs := []struct {
		name string
		down func(t *testing.T, f *fixture)
	}{
		{"stopped", func(_ *testing.T, f *fixture) { f.hub.Stop() }},
		{"silent", func(t *testing.T, f *fixture) { f.silenceHub(t) }},
	}
	tests := []struct {
		method, path, body string
		// registered inserts demo into the store before the request, for a
		// route of a registered workspace.
		registered bool
		// recorded records a rule of that name for demo before the request.
		recorded string
	}{
		{method: http.MethodPost, path: "/api/v1/workspaces", body: `{"slug": "demo", "health_command": "true"}`},
		{method: http.MethodGet, path: "/api/v1/workspaces/demo", registered: true},
		{method: http.MethodPost, path: "/api/v1/workspaces/demo/policy/rules", body: `{"globs": ["deploy/**"]}`, registered: true},
		{method: http.MethodDelete, path: "/api/v1/workspaces/demo/policy/rules/x", registered: true, recorded: "x"},
	}
	for _, down := range hubs {
		for _, tt := range tests {
			t.Run(down.name+" "+tt.method+" "+tt.path, func(t *testing.T) {
				t.Parallel()
				f := newFixture(t, nil)
				wantRecorded := 0
				if tt.registered {
					f.insert(t, "demo")
				}
				if tt.recorded != "" {
					f.recordRule(t, "demo", tt.recorded)
					wantRecorded = 1
				}
				down.down(t, f)
				// Bounds the request, so that a hub that is never given up
				// on fails the row rather than the whole test binary.
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()

				r := f.do(ctx, tt.method, tt.path, tt.body)

				if r.status != http.StatusServiceUnavailable || r.body["error_type"] != "hub_unavailable" {
					t.Fatalf("answer = %d %s, want 503 hub_unavailable", r.status, r.raw)
				}
				if msg, _ := r.body["message"].(string); msg == "" {
					t.Error("the answer has no message")
				}
				if !tt.registered {
					f.assertNoneStored(t)
					return
				}
				if n := f.recordedRules(t, "demo"); n != wantRecorded {
					t.Errorf("%d rules are recorded for demo, want %d", n, wantRecorded)
				}
				// The list does not ask the hub, so it answers while the hub is
				// down.
				list := f.do(context.Background(), http.MethodGet, "/api/v1/workspaces", "")
				if list.status != http.StatusOK || !slices.Equal(slugsOf(t, list), []string{"demo"}) {
					t.Errorf("the list answered %d %s while the hub is down, want 200 with demo", list.status, list.raw)
				}
			})
		}
	}
}

func TestRunHealth_RunsTheHealthCommandAtTheCommit(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	out := filepath.Join(t.TempDir(), "out")
	health := `git rev-parse HEAD > '` + out + `'; echo ran`
	if r := f.register(t, map[string]any{"slug": "demo", "health_command": health}); r.status != http.StatusCreated {
		t.Fatalf("registration answered %d %s, want 201", r.status, r.raw)
	}
	next := f.repo.AddCommit(t, map[string]string{"NEXT.md": "next\n"}, "Add the next file")

	res, err := f.svc.RunHealth(context.Background(), "demo", next, 10)

	if err != nil {
		t.Fatalf("RunHealth: %v", err)
	}
	if res.ExitCode != 0 || res.TimedOut || !slices.Equal(res.Tail, []string{"ran"}) {
		t.Errorf("RunHealth = %+v, want exit 0 with the tail [ran]", res)
	}
	if ran, _ := os.ReadFile(out); string(ran) != next+"\n" {
		t.Errorf("the health command ran at %q, want %s", ran, next)
	}
	if _, err := f.svc.RunHealth(context.Background(), "nope", next, 10); !errors.Is(err, workspace.ErrNotFound) {
		t.Errorf("RunHealth of an unregistered slug = %v, want ErrNotFound", err)
	}
}

func TestRemoveStrayClones_KeepsOnlyTheClonesOfRegisteredWorkspaces(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	if r := f.register(t, map[string]any{"slug": "demo", "health_command": "true"}); r.status != http.StatusCreated {
		t.Fatalf("registration answered %d %s, want 201", r.status, r.raw)
	}
	ghost := filepath.Join(f.clones, "ghost")
	if err := os.MkdirAll(filepath.Join(ghost, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	// What clones_dir holds besides clones is the person's, however the
	// directory was chosen.
	notes := filepath.Join(f.clones, "notes.txt")
	src := filepath.Join(f.clones, "src")
	if err := os.WriteFile(notes, []byte("mine\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(src, "project"), 0o755); err != nil {
		t.Fatal(err)
	}

	if err := f.svc.RemoveStrayClones(context.Background()); err != nil {
		t.Fatalf("RemoveStrayClones: %v", err)
	}

	if _, err := os.Stat(filepath.Join(f.clones, "demo", "README.md")); err != nil {
		t.Errorf("the clone of demo: %v, want it kept", err)
	}
	if _, err := os.Stat(ghost); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("the clone of an unregistered slug is left (stat error %v), want it removed", err)
	}
	for _, kept := range []string{notes, src} {
		if _, err := os.Stat(kept); err != nil {
			t.Errorf("%s, which is no clone: %v, want it kept", kept, err)
		}
	}
}

func TestRegister_EndsTheHealthCommandWhenTheClientGoesAway(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	dir := t.TempDir()
	started, late := filepath.Join(dir, "started"), filepath.Join(dir, "late")
	health := fmt.Sprintf(`touch '%s'; sleep 0.3; touch '%s'`, started, late)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan answer, 1)
	go func() {
		result <- f.do(ctx, http.MethodPost, "/api/v1/workspaces", `{"slug": "demo", "health_command": `+strconv.Quote(health)+`}`)
	}()
	gate{started: started}.awaitStart(t)

	cancel()

	select {
	case <-result:
	case <-time.After(5 * time.Second):
		t.Fatal("the registration did not end within 5s of the client going away")
	}
	time.Sleep(500 * time.Millisecond)
	if _, err := os.Stat(late); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("the health command ran on after the client went away (stat error %v)", err)
	}
	f.assertNoneStored(t)
}

// Once the hub took the adopting write, the workspace is stored though the
// client went away: otherwise the hub's policy would be adopted for a
// workspace nightshift does not know.
func TestRegister_FinishesOnceTheHubTookThePolicyThoughTheClientWentAway(t *testing.T) {
	t.Parallel()
	f := newFixture(t, nil)
	f.hub.SetPolicy("demo", hubtest.Policy{})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	f.hub.OnPolicyPut("demo", cancel)

	f.do(ctx, http.MethodPost, "/api/v1/workspaces", `{"slug": "demo", "health_command": "true", "adopt_policy": true}`)

	if p := f.hub.Policy("demo"); p.Revision != 1 {
		t.Fatalf("the hub's policy is at revision %d, want the adopted 1", p.Revision)
	}
	if _, err := f.svc.Get(context.Background(), "demo"); err != nil {
		t.Errorf("Get(demo) after the adoption: %v, want the workspace stored", err)
	}
}
