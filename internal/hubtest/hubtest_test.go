package hubtest_test

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"syscall"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/hubtest"
)

func TestHub_StartAfterStopServesTheSameAddress(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	url := h.URL() + "/readyz"

	h.Stop()
	if _, err := http.Get(url); !errors.Is(err, syscall.ECONNREFUSED) {
		t.Fatalf("GET after Stop: %v, want connection refused", err)
	}

	h.Start(t)
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("GET after Start: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("readyz answered %d, want 200", resp.StatusCode)
	}
	if n := len(h.Requests()); n != 1 {
		t.Errorf("the hub logged %d requests, want 1", n)
	}
}

func TestHub_GitEndpointServesAGitTokenAndCountsFetches(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	repo := h.AddRepository(t, "acme", "demo")
	worker, _ := h.GoodTokens()
	noGit := h.AddToken(hubtest.Token{Permissions: []string{"workspaces:read"}})
	get := func(pat string) *http.Response {
		t.Helper()
		req, _ := http.NewRequest(http.MethodGet, repo.URL()+"/info/refs?service=git-upload-pack", nil)
		if pat != "" {
			req.SetBasicAuth("x", pat)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("GET info/refs: %v", err)
		}
		resp.Body.Close()
		return resp
	}

	for _, pat := range []string{"", noGit} {
		if resp := get(pat); resp.StatusCode != http.StatusUnauthorized || resp.Header.Get("WWW-Authenticate") == "" {
			t.Errorf("with %q: %d, WWW-Authenticate %q; want 401 asking for Basic", pat, resp.StatusCode, resp.Header.Get("WWW-Authenticate"))
		}
	}
	if resp := get(worker); resp.StatusCode != http.StatusOK {
		t.Errorf("with the worker token: %d, want 200", resp.StatusCode)
	}
	if resp, err := http.Get(h.URL() + "/readyz"); err == nil {
		resp.Body.Close()
	}
	repo.SetAnswer(http.StatusServiceUnavailable)
	if resp := get(worker); resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("scripted: %d, want 503", resp.StatusCode)
	}

	fetches := h.Fetches()
	if len(fetches) != 2 || len(fetches[0].Requests) != 3 || len(fetches[1].Requests) != 1 {
		t.Fatalf("fetches %+v, want two: three requests, then one", fetches)
	}
	if fetches[0].Repository != repo.Path() || fetches[0].Requests[2].Basic != worker || fetches[0].Requests[2].Status != http.StatusOK {
		t.Errorf("first fetch %+v, want %s ending in the worker's 200", fetches[0], repo.Path())
	}
}

// policyRequest sends a request to the policy of demo and decodes the
// answer's JSON.
func policyRequest(t *testing.T, h *hubtest.Hub, method, pat, ifMatch, body string) (int, http.Header, map[string]any) {
	t.Helper()
	req, err := http.NewRequest(method, h.URL()+"/api/v1/workspaces/demo/policy", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+pat)
	if ifMatch != "" {
		req.Header.Set("If-Match", ifMatch)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s policy: %v", method, err)
	}
	defer resp.Body.Close()
	var answer map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&answer); err != nil {
		t.Fatalf("%s policy: decoding the answer: %v", method, err)
	}
	return resp.StatusCode, resp.Header, answer
}

func TestHub_PolicyGetAnswersTheNormalFormWithItsRevision(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	noScope := h.AddToken(hubtest.Token{Permissions: []string{"git:read"}})

	status, header, body := policyRequest(t, h, http.MethodGet, approver, "", "")
	if status != http.StatusOK || header.Get("ETag") != `"0"` {
		t.Fatalf("GET with changes:read: %d, ETag %q; want 200, \"0\"", status, header.Get("ETag"))
	}
	want := map[string]any{
		"approvals": float64(0), "approvers": []any{}, "checks": []any{}, "fresh_checks": false, "block": false,
	}
	if !reflect.DeepEqual(body["default"], want) || !reflect.DeepEqual(body["rules"], []any{}) || body["revision"] != float64(0) {
		t.Errorf("GET of a policy never set = %v, want revision 0, an empty default in normal form and no rules", body)
	}

	h.SetPolicy("demo", hubtest.Policy{Revision: 3})
	if rev := h.ReplacePolicy("demo", hubtest.Policy{Rules: []hubtest.Rule{{Name: "docs", Paths: []string{"docs/**"}}}}); rev != 4 {
		t.Errorf("ReplacePolicy = %d, want 4", rev)
	}
	status, header, body = policyRequest(t, h, http.MethodGet, worker, "", "")
	rules, _ := body["rules"].([]any)
	if status != http.StatusOK || header.Get("ETag") != `"4"` || len(rules) != 1 {
		t.Fatalf("GET after ReplacePolicy: %d, ETag %q, %v; want 200, \"4\" and the rule docs", status, header.Get("ETag"), body)
	}
	if got := rules[0].(map[string]any); !reflect.DeepEqual(got["exclude"], []any{}) || got["require"] == nil {
		t.Errorf("rule %v, want its missing members in normal form", got)
	}

	if status, _, body := policyRequest(t, h, http.MethodGet, noScope, "", ""); status != http.StatusForbidden {
		t.Errorf("GET without a changes scope: %d %v, want 403", status, body)
	}
}

func TestHub_PolicyGetHookRunsBeforeTheAnswerArrives(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, _ := h.GoodTokens()
	h.OnPolicyGet("demo", func() { h.ReplacePolicy("demo", hubtest.Policy{}) })

	status, header, body := policyRequest(t, h, http.MethodGet, worker, "", "")
	if status != http.StatusOK || header.Get("ETag") != `"0"` || body["revision"] != float64(0) {
		t.Errorf("GET with the hook: %d, ETag %q, %v; want the policy as it was, revision 0", status, header.Get("ETag"), body)
	}
	if got := h.Policy("demo").Revision; got != 1 {
		t.Errorf("after the GET the policy is at revision %d, want 1", got)
	}
	if status, _, _ := policyRequest(t, h, http.MethodPut, worker, "0", `{}`); status != http.StatusPreconditionFailed {
		t.Errorf("PUT against the revision read: %d, want 412", status)
	}
}

func TestHub_PolicyPutChecksTheRevisionAndTheFormat(t *testing.T) {
	t.Parallel()
	long := strings.Repeat("a", 256)
	rules := func(n int) string {
		var b strings.Builder
		for i := range n {
			if i > 0 {
				b.WriteString(",")
			}
			fmt.Fprintf(&b, `{"name":"r%d","paths":["r/**"]}`, i)
		}
		return `{"rules":[` + b.String() + `]}`
	}
	tests := []struct {
		name    string
		pat     string // "worker" when empty
		ifMatch string
		body    string
		script  func(h *hubtest.Hub)
		status  int
		errType string
		details map[string]any
	}{
		{name: "quoted revision", ifMatch: `"2"`, body: `{"rules":[{"name":"docs","paths":["docs/**"]}]}`, status: 200},
		{name: "bare revision", ifMatch: "2", body: `{"default":{"approvals":1,"approvers":["owner"]}}`, status: 200},
		{name: "stale revision", ifMatch: "1", body: `{}`, status: 412, errType: "revision_mismatch"},
		{name: "no If-Match", body: `{}`, status: 428, errType: "precondition_required"},
		{name: "upper-case name", ifMatch: "2", body: `{"rules":[{"name":"Deploy","paths":["x"]}]}`, status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": "Deploy", "index": float64(0), "field": "name"}},
		{name: "name default", ifMatch: "2", body: `{"rules":[{"name":"default","paths":["x"]}]}`, status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": "default", "index": float64(0), "field": "name"}},
		{name: "name of 64 characters", ifMatch: "2", body: `{"rules":[{"name":"` + strings.Repeat("n", 64) + `","paths":["x"]}]}`, status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": strings.Repeat("n", 64), "index": float64(0), "field": "name"}},
		{name: "name taken", ifMatch: "2", body: `{"rules":[{"name":"a","paths":["x"]},{"name":"a","paths":["y"]}]}`, status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": "a", "index": float64(1), "field": "name"}},
		{name: "empty glob", ifMatch: "2", body: `{"rules":[{"name":"a","paths":[""]}]}`, status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": "a", "index": float64(0), "field": "paths[0]"}},
		{name: "glob of 256 characters", ifMatch: "2", body: `{"rules":[{"name":"a","paths":["` + long + `"]}]}`, status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": "a", "index": float64(0), "field": "paths[0]"}},
		{name: "glob with a leading slash", ifMatch: "2", body: `{"rules":[{"name":"a","paths":["ok","/etc/**"]}]}`, status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": "a", "index": float64(0), "field": "paths[1]"}},
		{name: "exclude with a leading slash", ifMatch: "2", body: `{"rules":[{"name":"a","paths":["ok"],"exclude":["/tmp"]}]}`, status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": "a", "index": float64(0), "field": "exclude[0]"}},
		{name: "201 rules", ifMatch: "2", body: rules(201), status: 400, errType: "invalid_policy",
			details: map[string]any{"rule": "", "index": float64(200), "field": "rules"}},
		{name: "200 rules", ifMatch: "2", body: rules(200), status: 200},
		{name: "without workspaces:write", pat: "approver", ifMatch: "2", body: `{}`, status: 403, errType: "insufficient_scope"},
		{name: "not the owner", pat: "stranger", ifMatch: "2", body: `{}`, status: 403, errType: "forbidden"},
		{name: "scripted", ifMatch: "2", body: `{}`, script: func(h *hubtest.Hub) {
			h.FailNextPolicyPut("demo", http.StatusBadRequest, "invalid_policy", "glob a[b is malformed",
				map[string]any{"rule": "nightshift-1", "index": 0, "field": "paths[0]"})
		}, status: 400, errType: "invalid_policy", details: map[string]any{"rule": "nightshift-1", "index": float64(0), "field": "paths[0]"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			worker, approver := h.GoodTokens()
			pat := map[string]string{
				"":         worker,
				"approver": approver,
				"stranger": h.AddToken(hubtest.Token{UserID: "u_stranger", Permissions: hubtest.WorkerScopes()}),
			}[tt.pat]
			h.SetPolicy("demo", hubtest.Policy{Revision: 2})
			if tt.script != nil {
				tt.script(h)
			}

			status, header, body := policyRequest(t, h, http.MethodPut, pat, tt.ifMatch, tt.body)
			if status != tt.status {
				t.Fatalf("PUT: %d %v, want %d", status, body, tt.status)
			}
			if puts := h.PolicyPuts("demo"); len(puts) != 1 || puts[0].IfMatch != tt.ifMatch || string(puts[0].Body) != tt.body {
				t.Errorf("PolicyPuts = %+v, want the one PUT with If-Match %q", puts, tt.ifMatch)
			}
			rev := h.Policy("demo").Revision
			if tt.status == http.StatusOK {
				if rev != 3 || body["revision"] != float64(3) || header.Get("ETag") != `"3"` {
					t.Errorf("accepted: the hub is at %d, answered %v with ETag %q; want revision 3", rev, body, header.Get("ETag"))
				}
				return
			}
			if rev != 2 {
				t.Errorf("refused: the hub's policy is at revision %d, want 2", rev)
			}
			envelope, _ := body["error"].(map[string]any)
			if envelope["error_type"] != tt.errType || envelope["message"] == "" {
				t.Errorf("error %v, want error_type %q with a message", envelope, tt.errType)
			}
			if tt.details != nil && !reflect.DeepEqual(envelope["details"], tt.details) {
				t.Errorf("details %v, want %v", envelope["details"], tt.details)
			}
			if tt.script != nil {
				if status, _, _ := policyRequest(t, h, http.MethodPut, pat, "2", `{}`); status != http.StatusOK {
					t.Errorf("the PUT after the scripted one: %d, want 200", status)
				}
			}
		})
	}
}
