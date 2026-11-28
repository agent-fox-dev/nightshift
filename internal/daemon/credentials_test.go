package daemon_test

import (
	"slices"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/daemon"
	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
)

// without returns scopes less drop.
func without(scopes []string, drop ...string) []string {
	return slices.DeleteFunc(slices.Clone(scopes), func(s string) bool { return slices.Contains(drop, s) })
}

// tokenPair is the worker and the approver token a refusal adds to the hub.
type tokenPair struct{ worker, approver hubtest.Token }

// refusal is one way the credentials check fails.
type refusal struct {
	name string
	// change alters the good pair before it is added to the hub.
	change func(*tokenPair)
	// worker and approver, when set, replace the texts of the added tokens.
	worker, approver func(worker, approver string) string
	want             string
	// noRequest says the check must not send either token to the hub.
	noRequest bool
}

// refusals are the ways the credentials check fails. Each changes one thing
// (or two, to show which reason wins) of the good pair: a token with the
// worker's scopes and one with the approver's.
func refusals(workerScopes []string) []refusal {
	text := func(s string) func(string, string) string { return func(string, string) string { return s } }
	workerText := func(w, _ string) string { return w }
	rows := []refusal{
		{name: "worker is an api key", worker: text("parley_ab_cd"),
			want: "worker token is not a personal access token", noRequest: true},
		{name: "worker has no secret", worker: text("parley_pat_tok9"),
			want: "worker token is not a personal access token", noRequest: true},
		{name: "approver is an admin key", approver: text("parley_admin_xyz"),
			want: "approver token is not a personal access token", noRequest: true},
		{name: "approver has no token id", approver: text("parley_pat__secret"),
			want: "approver token is not a personal access token", noRequest: true},
		{name: "neither is a personal access token", worker: text("parley_ab_cd"), approver: text("parley_admin_xyz"),
			want: "worker token is not a personal access token", noRequest: true},
		{name: "same token", approver: workerText,
			want: "worker and approver are the same token", noRequest: true},
		{name: "same text that is not a personal access token", worker: text("parley_ab_cd"), approver: text("parley_ab_cd"),
			want: "worker token is not a personal access token", noRequest: true},

		{name: "worker revoked", change: func(p *tokenPair) { p.worker.Revoked = true },
			want: "worker token: 401 unauthenticated"},
		{name: "worker unknown to the hub", worker: text("parley_pat_nosuch_secret"),
			want: "worker token: 401 unauthenticated"},
		{name: "approver revoked", change: func(p *tokenPair) { p.approver.Revoked = true },
			want: "approver token: 401 unauthenticated"},
		{name: "both revoked", change: func(p *tokenPair) { p.worker.Revoked, p.approver.Revoked = true, true },
			want: "worker token: 401 unauthenticated"},
		{name: "approver revoked and worker bound", change: func(p *tokenPair) { p.approver.Revoked, p.worker.Workspace = true, "ws1" },
			want: "approver token: 401 unauthenticated"},

		{name: "worker bound", change: func(p *tokenPair) { p.worker.Workspace = "ws1" },
			want: "worker token is bound to a workspace"},
		{name: "worker object names a workspace", change: func(p *tokenPair) { p.worker.Workspace, p.worker.ReadsBound = "ws1", true },
			want: "worker token is bound to a workspace"},
		{name: "worker bound and lacking tokens:manage", change: func(p *tokenPair) {
			p.worker.Workspace, p.worker.Permissions = "ws1", without(workerScopes, "tokens:manage")
		}, want: "worker token is bound to a workspace"},
		{name: "worker bound and holding changes:approve", change: func(p *tokenPair) {
			p.worker.Workspace, p.worker.ReadsBound = "ws1", true
			p.worker.Permissions = append(slices.Clone(workerScopes), "changes:approve")
		}, want: "worker token is bound to a workspace"},

		{name: "worker holds changes:approve", change: func(p *tokenPair) {
			p.worker.Permissions = append(slices.Clone(workerScopes), "changes:approve")
		}, want: "worker token must not hold changes:approve"},
		{name: "worker holds changes:approve and lacks workspaces:write", change: func(p *tokenPair) {
			p.worker.Permissions = append(without(workerScopes, "workspaces:write"), "changes:approve")
		}, want: "worker token must not hold changes:approve"},
		{name: "worker lacks tokens:manage and holds changes:approve", change: func(p *tokenPair) {
			p.worker.Permissions = append(without(workerScopes, "tokens:manage"), "changes:approve")
		}, want: "worker token lacks tokens:manage"},

		{name: "worker lacks tokens:manage and workspaces:write", change: func(p *tokenPair) {
			p.worker.Permissions = without(workerScopes, "tokens:manage", "workspaces:write")
		}, want: "worker token lacks tokens:manage"},
		{name: "worker holds tokens:read instead of tokens:manage", change: func(p *tokenPair) {
			p.worker.Permissions = append(without(workerScopes, "tokens:manage"), "tokens:read")
		}, want: "worker token lacks tokens:manage"},
		{name: "worker lacks sessions:write and git:write", change: func(p *tokenPair) {
			p.worker.Permissions = without(workerScopes, "sessions:write", "git:write")
		}, want: "worker token lacks git:write"},
		{name: "approver lacks changes:read", change: func(p *tokenPair) { p.approver.Permissions = []string{"changes:approve"} },
			want: "approver token lacks changes:read"},
		{name: "approver lacks changes:approve", change: func(p *tokenPair) { p.approver.Permissions = []string{"changes:read"} },
			want: "approver token lacks changes:approve"},
		{name: "approver lacks both scopes", change: func(p *tokenPair) { p.approver.Permissions = []string{"changes:write"} },
			want: "approver token lacks changes:read"},
		{name: "worker and approver each lack a scope", change: func(p *tokenPair) {
			p.worker.Permissions = without(workerScopes, "audit:read")
			p.approver.Permissions = []string{"changes:read"}
		}, want: "worker token lacks audit:read"},

		{name: "approver of another user", change: func(p *tokenPair) { p.approver.UserID = "u_other" },
			want: "worker token: 404 not_found"},
	}
	for _, scope := range workerScopes {
		rows = append(rows, refusal{name: "worker lacks " + scope, change: func(p *tokenPair) {
			p.worker.Permissions = without(workerScopes, scope)
		}, want: "worker token lacks " + scope})
	}
	return rows
}

func TestCheckCredentials_Refusals(t *testing.T) {
	t.Parallel()
	workerScopes, approverScopes := hubtest.WorkerScopes(), hubtest.ApproverScopes()

	for _, tt := range refusals(workerScopes) {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			pair := tokenPair{
				worker:   hubtest.Token{Permissions: workerScopes},
				approver: hubtest.Token{Permissions: approverScopes},
			}
			if tt.change != nil {
				tt.change(&pair)
			}
			worker, approver := h.AddToken(pair.worker), h.AddToken(pair.approver)
			w, a := worker, approver
			if tt.worker != nil {
				w = tt.worker(worker, approver)
			}
			if tt.approver != nil {
				a = tt.approver(worker, approver)
			}

			err := daemon.CheckCredentials(t.Context(), hub.New(h.URL(), w, a), w, a)

			if err == nil || err.Error() != tt.want {
				t.Errorf("CheckCredentials = %v, want %q", err, tt.want)
			}
			if n := len(h.Requests()); tt.noRequest && n != 0 {
				t.Errorf("the hub saw %d requests, want none", n)
			}
			if n := len(h.Bearing(a)); n > 1 {
				t.Errorf("the approver token was sent %d times, want at most once", n)
			}
		})
	}
}

func TestCheckCredentials_AcceptsGoodTokens(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()

	if err := daemon.CheckCredentials(t.Context(), hub.New(h.URL(), worker, approver), worker, approver); err != nil {
		t.Fatalf("CheckCredentials = %v, want nil", err)
	}
	sent := h.Bearing(approver)
	if len(sent) != 1 {
		t.Fatalf("the approver token was sent %d times, want once", len(sent))
	}
	if sent[0].Status != 403 || sent[0].ErrorType != "insufficient_scope" {
		t.Errorf("the approver's request was answered %d %s, want 403 insufficient_scope", sent[0].Status, sent[0].ErrorType)
	}
}
