package workspace_test

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/hubtest"
	"github.com/agent-fox-dev/nightshift/internal/policy"
)

const (
	rulesPath        = "/api/v1/workspaces/demo/policy/rules"
	changedMeanwhile = "policy changed meanwhile; run the command again"
	notWritten       = "not a rule nightshift wrote"
)

// docsRule is a rule the person wrote, which requires the three checks.
func docsRule() hubtest.Rule {
	return hubtest.Rule{Name: "docs", Paths: []string{"docs/**"}, Require: complete()}
}

// newPolicyFixture is the fixture with demo stored and its policy at
// revision 3 holding the person's rule docs.
func newPolicyFixture(t *testing.T) *fixture {
	t.Helper()
	f := newFixture(t, nil)
	f.hub.SetPolicy("demo", hubtest.Policy{Revision: 3, Default: complete(), Rules: []hubtest.Rule{docsRule()}})
	f.insert(t, "demo")
	return f
}

// protect posts a protection of demo with the members of body.
func (f *fixture) protect(t *testing.T, body map[string]any) answer {
	t.Helper()
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	return f.do(context.Background(), http.MethodPost, rulesPath, string(data))
}

// unprotect deletes the rule name of demo.
func (f *fixture) unprotect(name string) answer {
	return f.do(context.Background(), http.MethodDelete, rulesPath+"/"+name, "")
}

// puts is the number of PUTs of demo's policy the hub saw.
func (f *fixture) puts() int { return len(f.hub.PolicyPuts("demo")) }

func ruleNames(p hubtest.Policy) []string {
	names := []string{}
	for _, r := range p.Rules {
		names = append(names, r.Name)
	}
	return names
}

func TestProtect_WritesTheRuleAtTheTopOfThePolicy(t *testing.T) {
	t.Parallel()
	approval := hubtest.Requirement{Approvals: 1, Approvers: []string{"owner"}, Checks: slices.Clone(policy.Checks), FreshChecks: true}
	block := hubtest.Requirement{Approvers: []string{"owner"}, Checks: []string{}, Block: true}
	tests := []struct {
		name     string
		body     map[string]any
		wantRule hubtest.Rule
	}{
		{
			name:     "approval",
			body:     map[string]any{"globs": []string{"deploy/**", "ops/*.yaml"}},
			wantRule: hubtest.Rule{Name: "nightshift-1", Paths: []string{"deploy/**", "ops/*.yaml"}, Exclude: []string{}, Require: approval},
		},
		{
			name:     "block",
			body:     map[string]any{"globs": []string{"deploy/secrets/**"}, "block": true},
			wantRule: hubtest.Rule{Name: "nightshift-1", Paths: []string{"deploy/secrets/**"}, Exclude: []string{}, Require: block},
		},
		{
			name:     "named",
			body:     map[string]any{"globs": []string{"x/**"}, "name": "deploy"},
			wantRule: hubtest.Rule{Name: "deploy", Paths: []string{"x/**"}, Exclude: []string{}, Require: approval},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newPolicyFixture(t)
			docs := f.hub.Policy("demo").Rules[0]

			r := f.protect(t, tt.body)

			if r.status != http.StatusCreated {
				t.Fatalf("answer = %d %s, want 201", r.status, r.raw)
			}
			// The answer's rule is compared as JSON with the rule the hub
			// holds, its name, paths and requirement.
			var want map[string]any
			json.Unmarshal([]byte(jsonOf(t, map[string]any{
				"rule":     map[string]any{"name": tt.wantRule.Name, "paths": tt.wantRule.Paths, "require": tt.wantRule.Require},
				"revision": 4,
			})), &want)
			if !reflect.DeepEqual(r.body, want) {
				t.Errorf("answer = %v, want %v", r.body, want)
			}
			p := f.hub.Policy("demo")
			if p.Revision != 4 || len(p.Rules) != 2 {
				t.Fatalf("the hub's policy is %+v, want revision 4 with two rules", p)
			}
			if !reflect.DeepEqual(p.Rules[0], tt.wantRule) {
				t.Errorf("the hub's first rule = %+v, want %+v", p.Rules[0], tt.wantRule)
			}
			if !reflect.DeepEqual(p.Rules[1], docs) {
				t.Errorf("the hub's second rule = %+v, want the person's rule unchanged %+v", p.Rules[1], docs)
			}
			if puts := f.hub.PolicyPuts("demo"); len(puts) != 1 || strings.Trim(puts[0].IfMatch, `"`) != "3" {
				t.Errorf("the hub saw the PUTs %+v, want one with If-Match 3", puts)
			}
			if n := f.recordedRules(t, "demo"); n != 1 {
				t.Errorf("%d rules are recorded for demo, want 1", n)
			}
		})
	}
}

func jsonOf(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestProtect_RefusesAGlob(t *testing.T) {
	t.Parallel()
	const hubMessage = `rule "nightshift-1": paths[0]: the brackets of a class are not closed`
	tests := []struct {
		name  string
		globs []string
		// hubRefuses scripts the hub to refuse the write of the first glob.
		hubRefuses  bool
		wantMessage string
	}{
		{name: "empty", globs: []string{""}, wantMessage: `glob "" is refused: `},
		{name: "a leading slash", globs: []string{"deploy/**", "/etc/**"}, wantMessage: `glob "/etc/**" is refused: `},
		{name: "256 characters", globs: []string{strings.Repeat("a", 256)}, wantMessage: `glob "` + strings.Repeat("a", 256) + `" is refused: `},
		{name: "no glob", globs: []string{}},
		{name: "refused by the hub", globs: []string{"a[b"}, hubRefuses: true, wantMessage: `glob "a[b" is refused: ` + hubMessage},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newPolicyFixture(t)
			wantPuts := 0
			if tt.hubRefuses {
				f.hub.FailNextPolicyPut("demo", http.StatusBadRequest, "invalid_policy", hubMessage,
					map[string]any{"rule": "nightshift-1", "index": 0, "field": "paths[0]"})
				wantPuts = 1
			}

			r := f.protect(t, map[string]any{"globs": tt.globs})

			if r.status != http.StatusBadRequest || r.body["error_type"] != "invalid_glob" {
				t.Fatalf("answer = %d %s, want 400 invalid_glob", r.status, r.raw)
			}
			msg, _ := r.body["message"].(string)
			switch {
			case tt.hubRefuses && msg != tt.wantMessage:
				t.Errorf("message = %q, want %q", msg, tt.wantMessage)
			case !tt.hubRefuses && (!strings.HasPrefix(msg, tt.wantMessage) || len(msg) == len(tt.wantMessage)):
				t.Errorf("message = %q, want %q followed by the reason", msg, tt.wantMessage)
			}
			if n := f.puts(); n != wantPuts {
				t.Errorf("the hub saw %d PUTs, want %d", n, wantPuts)
			}
			if rev := f.hub.Policy("demo").Revision; rev != 3 {
				t.Errorf("the hub's policy is at revision %d, want 3", rev)
			}
			if r := f.unprotect("nightshift-1"); r.status != http.StatusConflict || r.body["message"] != notWritten {
				t.Errorf("a later unprotect answered %d %s, want 409 %q", r.status, r.raw, notWritten)
			}
		})
	}
}

func TestProtect_RefusesAName(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name, ruleName string
		wantStatus     int
		wantType       string
		wantMessage    string
	}{
		{name: "taken", ruleName: "docs", wantStatus: http.StatusConflict, wantType: "conflict", wantMessage: "rule docs exists already"},
		{name: "default", ruleName: "default", wantStatus: http.StatusBadRequest, wantType: "invalid_request"},
		{name: "upper case", ruleName: "Deploy", wantStatus: http.StatusBadRequest, wantType: "invalid_request"},
		{name: "64 characters", ruleName: strings.Repeat("a", 64), wantStatus: http.StatusBadRequest, wantType: "invalid_request"},
		{name: "a space", ruleName: "a b", wantStatus: http.StatusBadRequest, wantType: "invalid_request"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newPolicyFixture(t)

			r := f.protect(t, map[string]any{"globs": []string{"x/**"}, "name": tt.ruleName})

			if r.status != tt.wantStatus || r.body["error_type"] != tt.wantType {
				t.Fatalf("answer = %d %s, want %d %s", r.status, r.raw, tt.wantStatus, tt.wantType)
			}
			if tt.wantMessage != "" && r.body["message"] != tt.wantMessage {
				t.Errorf("message = %q, want %q", r.body["message"], tt.wantMessage)
			}
			if n := f.puts(); n != 0 {
				t.Errorf("the hub saw %d PUTs, want none", n)
			}
			if n := f.recordedRules(t, "demo"); n != 0 {
				t.Errorf("%d rules are recorded for demo, want none", n)
			}
		})
	}
}

func TestProtect_RefusesAPolicyChangedMeanwhile(t *testing.T) {
	t.Parallel()
	written := hubtest.Rule{Name: "nightshift-1", Paths: []string{"deploy/**"},
		Require: hubtest.Requirement{Approvals: 1, Approvers: []string{"owner"}, Checks: slices.Clone(policy.Checks), FreshChecks: true}}
	tests := []struct {
		name string
		// recorded puts the rule nightshift-1 in the policy and records it
		// for demo before the request.
		recorded bool
		request  func(t *testing.T, f *fixture) answer
	}{
		{
			name: "protect",
			request: func(t *testing.T, f *fixture) answer {
				return f.protect(t, map[string]any{"globs": []string{"deploy/**"}})
			},
		},
		{
			name:     "unprotect of a recorded rule",
			recorded: true,
			request:  func(_ *testing.T, f *fixture) answer { return f.unprotect("nightshift-1") },
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newPolicyFixture(t)
			rules := []hubtest.Rule{docsRule()}
			wantRecorded := 0
			if tt.recorded {
				rules = []hubtest.Rule{written, docsRule()}
				f.hub.SetPolicy("demo", hubtest.Policy{Revision: 3, Default: complete(), Rules: rules})
				f.recordRule(t, "demo", "nightshift-1")
				wantRecorded = 1
			}
			// A person replaces the policy between the service's read and
			// its write.
			f.hub.OnPolicyGet("demo", func() {
				f.hub.ReplacePolicy("demo", hubtest.Policy{Default: complete(), Rules: rules})
			})

			r := tt.request(t, f)

			f.assertRefused(t, r, changedMeanwhile)
			if n := f.puts(); n != 1 {
				t.Errorf("the hub saw %d PUTs, want exactly one", n)
			}
			if n := f.recordedRules(t, "demo"); n != wantRecorded {
				t.Errorf("%d rules are recorded for demo, want %d", n, wantRecorded)
			}
		})
	}
}

func TestUnprotect_RemovesTheRuleAndForgetsIt(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		// dropped makes the hub drop the rule by itself, at revision 5,
		// before the unprotect.
		dropped bool
	}{
		{name: "the rule is in the policy"},
		{name: "the hub dropped the rule", dropped: true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newPolicyFixture(t)
			docs := f.hub.Policy("demo").Rules[0]
			if r := f.protect(t, map[string]any{"globs": []string{"deploy/**"}}); r.status != http.StatusCreated || r.body["revision"] != float64(4) {
				t.Fatalf("the protection answered %d %s, want 201 at revision 4", r.status, r.raw)
			}
			wantPuts := 2
			if tt.dropped {
				f.hub.ReplacePolicy("demo", hubtest.Policy{Default: complete(), Rules: []hubtest.Rule{docs}})
				wantPuts = 1
			}

			r := f.unprotect("nightshift-1")

			if r.status != http.StatusOK || !reflect.DeepEqual(r.body, map[string]any{"revision": float64(5)}) {
				t.Fatalf("answer = %d %s, want 200 {\"revision\": 5}", r.status, r.raw)
			}
			p := f.hub.Policy("demo")
			if p.Revision != 5 || len(p.Rules) != 1 || !reflect.DeepEqual(p.Rules[0], docs) {
				t.Errorf("the hub's policy is %+v, want revision 5 with the person's rule alone", p)
			}
			if n := f.puts(); n != wantPuts {
				t.Errorf("the hub saw %d PUTs, want %d", n, wantPuts)
			}
			if r := f.unprotect("nightshift-1"); r.status != http.StatusConflict || r.body["message"] != notWritten {
				t.Errorf("a second unprotect answered %d %s, want 409 %q", r.status, r.raw, notWritten)
			}
		})
	}
}

func TestUnprotect_RefusesARuleNightshiftDidNotWrite(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name, rule string
		setup      func(t *testing.T, f *fixture)
	}{
		{name: "the person's rule", rule: "docs"},
		{name: "a rule nowhere", rule: "nope"},
		{name: "a rule written for another workspace", rule: "nightshift-1", setup: func(t *testing.T, f *fixture) {
			f.insert(t, "other")
			f.recordRule(t, "other", "nightshift-1")
		}},
		{name: "a rule written before the workspace was removed", rule: "nightshift-1", setup: func(t *testing.T, f *fixture) {
			if r := f.protect(t, map[string]any{"globs": []string{"deploy/**"}}); r.status != http.StatusCreated {
				t.Fatalf("the protection answered %d %s, want 201", r.status, r.raw)
			}
			if err := f.svc.Remove(context.Background(), "demo"); err != nil {
				t.Fatalf("Remove: %v", err)
			}
			f.insert(t, "demo")
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			f := newPolicyFixture(t)
			if tt.setup != nil {
				tt.setup(t, f)
			}
			before, puts := f.hub.Policy("demo"), f.puts()

			r := f.unprotect(tt.rule)

			f.assertRefused(t, r, notWritten)
			if n := f.puts() - puts; n != 0 {
				t.Errorf("the hub saw %d PUTs, want none", n)
			}
			if p := f.hub.Policy("demo"); !reflect.DeepEqual(p, before) || !slices.Contains(ruleNames(p), "docs") {
				t.Errorf("the hub's policy is %+v, want it unchanged %+v", p, before)
			}
		})
	}
}
