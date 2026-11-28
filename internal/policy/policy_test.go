package policy_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"maps"
	"net/http"
	"reflect"
	"slices"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
	"github.com/agent-fox-dev/nightshift/internal/policy"
)

const (
	gate    = "nightshift-gate"
	review  = "nightshift-review"
	release = "nightshift-release"
)

// complete is a requirement that counts the three checks on the version
// itself.
func complete() policy.Requirement {
	return policy.Requirement{Approvers: []string{}, Checks: []string{gate, review, release}, FreshChecks: true}
}

func rule(name string, req policy.Requirement) policy.Rule {
	return policy.Rule{Name: name, Paths: []string{name + "/**"}, Exclude: []string{}, Require: req}
}

type missingCase struct {
	name string
	doc  policy.Document
	want []string
}

// missingCases builds the documents afresh on every call, so that a test
// can compare a document with an untouched twin.
func missingCases() []missingCase {
	return []missingCase{
		{"revision 0 with no rules", policy.Document{}, []string{"default"}},
		{"default complete and no rules", policy.Document{Revision: 1, Default: complete()}, nil},
		{"a rule with two of the three", policy.Document{Revision: 2, Default: complete(), Rules: []policy.Rule{
			rule("docs", policy.Requirement{Checks: []string{review, release}, FreshChecks: true}),
		}}, []string{"docs"}},
		{"a rule with the three and fresh_checks false", policy.Document{Revision: 2, Default: complete(), Rules: []policy.Rule{
			rule("docs", policy.Requirement{Checks: []string{gate, review, release}}),
		}}, []string{"docs"}},
		{"a blocking rule with no checks", policy.Document{Revision: 2, Default: complete(), Rules: []policy.Rule{
			rule("secrets", policy.Requirement{Block: true}),
		}}, nil},
		{"default and two rules lacking, in document order", policy.Document{Revision: 4, Rules: []policy.Rule{
			rule("zeta", policy.Requirement{Approvals: 1, Approvers: []string{"owner"}, Checks: []string{"spell"}}),
			rule("middle", complete()),
			rule("secrets", policy.Requirement{Block: true}),
			rule("alpha", policy.Requirement{Checks: []string{gate}, FreshChecks: true}),
		}}, []string{"default", "zeta", "alpha"}},
		{"a rule naming the three among other checks", policy.Document{Revision: 2, Default: complete(), Rules: []policy.Rule{
			rule("src", policy.Requirement{Checks: []string{"spell", release, "lint", gate, review}, FreshChecks: true}),
		}}, nil},
	}
}

func TestMissing_NamesTheRequirementsLackingTheChecks(t *testing.T) {
	t.Parallel()
	for _, tt := range missingCases() {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := policy.Missing(tt.doc); !slices.Equal(got, tt.want) {
				t.Errorf("Missing = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestAdopt_LeavesNothingMissingAndTheInputUntouched(t *testing.T) {
	t.Parallel()
	twins := missingCases()
	for i, tt := range missingCases() {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			adopted := policy.Adopt(tt.doc)
			if got := policy.Missing(adopted); len(got) != 0 {
				t.Errorf("Missing(Adopt(doc)) = %q, want none", got)
			}
			if !reflect.DeepEqual(tt.doc, twins[i].doc) {
				t.Errorf("Adopt changed its input to %+v", tt.doc)
			}
		})
	}
}

func TestAdopt_AppendsTheMissingChecksAndChangesNothingElse(t *testing.T) {
	t.Parallel()
	docs := policy.Rule{
		Name: "docs", Paths: []string{"docs/**"}, Exclude: []string{"docs/tmp/**"}, Targets: []string{"main"},
		Require: policy.Requirement{Approvals: 1, Approvers: []string{"member"}, Checks: []string{"spell", review}},
	}
	secrets := rule("secrets", policy.Requirement{Approvers: []string{}, Checks: []string{}, Block: true})
	doc := policy.Document{Revision: 2, Default: policy.Requirement{Approvers: []string{}, Checks: []string{}}, Rules: []policy.Rule{docs, secrets}}

	got := policy.Adopt(doc)

	wantDocs := docs
	wantDocs.Require.Checks = []string{"spell", review, gate, release}
	wantDocs.Require.FreshChecks = true
	want := policy.Document{Revision: 2, Default: complete(), Rules: []policy.Rule{wantDocs, secrets}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Adopt =\n%+v\nwant\n%+v", got, want)
	}
}

func TestSensitive_NamesTheRulesThatNeedAnApprovalOrBlock(t *testing.T) {
	t.Parallel()
	approve := func(n int) policy.Requirement {
		return policy.Requirement{Approvals: n, Approvers: []string{"owner"}, Checks: []string{gate, review, release}, FreshChecks: true}
	}
	block := policy.Requirement{Block: true}
	tests := []struct {
		name string
		doc  policy.Document
		want []policy.SensitiveRule
	}{
		{"no rules and an empty default", policy.Document{}, []policy.SensitiveRule{}},
		{"a rule with approvals 1", policy.Document{Default: complete(), Rules: []policy.Rule{rule("deploy", approve(1))}},
			[]policy.SensitiveRule{{Name: "deploy", Paths: []string{"deploy/**"}, Approvals: 1}}},
		{"a rule that blocks", policy.Document{Default: complete(), Rules: []policy.Rule{rule("secrets", block)}},
			[]policy.SensitiveRule{{Name: "secrets", Paths: []string{"secrets/**"}, Block: true}}},
		{"a rule with checks only", policy.Document{Default: complete(), Rules: []policy.Rule{rule("docs", complete())}},
			[]policy.SensitiveRule{}},
		{"default with approvals 2", policy.Document{Default: approve(2)},
			[]policy.SensitiveRule{{Name: "default", Paths: []string{}, Approvals: 2}}},
		{"a mix, the rules in document order and then default", policy.Document{Default: approve(1), Rules: []policy.Rule{
			rule("secrets", block), rule("docs", complete()), rule("deploy", approve(2)),
		}}, []policy.SensitiveRule{
			{Name: "secrets", Paths: []string{"secrets/**"}, Block: true},
			{Name: "deploy", Paths: []string{"deploy/**"}, Approvals: 2},
			{Name: "default", Paths: []string{}, Approvals: 1},
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			// Compared as JSON, the form the daemon answers them in, so that
			// an empty list is [] and the default's paths are [].
			got, want := jsonOf(t, policy.Sensitive(tt.doc)), jsonOf(t, tt.want)
			if got != want {
				t.Errorf("Sensitive = %s, want %s", got, want)
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

// document is the fake hub's policy as the policy package reads it.
func document(t *testing.T, p hubtest.Policy) policy.Document {
	t.Helper()
	var doc policy.Document
	if err := json.Unmarshal([]byte(jsonOf(t, p)), &doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func newHub(t *testing.T) (*hubtest.Hub, *hub.Client, string) {
	t.Helper()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	return h, hub.New(h.URL(), worker, approver), worker
}

// stored is a policy at revision 3 with a rule that has targets and one
// that has none.
func stored() hubtest.Policy {
	return hubtest.Policy{
		Revision: 3,
		Default:  hubtest.Requirement{Approvals: 1, Approvers: []string{"owner"}, Checks: []string{"spell"}},
		Rules: []hubtest.Rule{
			{Name: "docs", Paths: []string{"docs/**"}, Exclude: []string{"docs/tmp/**"}, Require: hubtest.Requirement{Approvers: []string{}, Checks: []string{gate}, FreshChecks: true}},
			{Name: "secrets", Paths: []string{"secrets/**"}, Exclude: []string{}, Targets: []string{"main"}, Require: hubtest.Requirement{Approvers: []string{}, Checks: []string{}, Block: true}},
		},
	}
}

func TestRead_ReturnsTheHubsPolicyWithItsRevision(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		set  *hubtest.Policy
		want hubtest.Policy
	}{
		{"never set", nil, hubtest.Policy{Default: hubtest.Requirement{Approvers: []string{}, Checks: []string{}}, Rules: []hubtest.Rule{}}},
		{"set at revision 3", new(stored()), stored()},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h, c, worker := newHub(t)
			if tt.set != nil {
				h.SetPolicy("demo", *tt.set)
			}
			doc, err := policy.Read(t.Context(), c, "demo")
			if err != nil {
				t.Fatalf("Read: %v", err)
			}
			if got, want := jsonOf(t, doc), jsonOf(t, document(t, tt.want)); got != want {
				t.Errorf("Read = %s, want %s", got, want)
			}
			if r := h.Bearing(worker); len(r) != 1 || r[0].Method != http.MethodGet || r[0].Path != "/api/v1/workspaces/demo/policy" {
				t.Errorf("the hub saw %+v with the worker token, want one GET of the policy", r)
			}
		})
	}
}

func TestRead_ReturnsAnUnreachableHubAsNoHubError(t *testing.T) {
	t.Parallel()
	h, c, _ := newHub(t)
	h.Stop()
	_, err := policy.Read(t.Context(), c, "demo")
	var he *hub.Error
	if err == nil || errors.As(err, &he) {
		t.Errorf("Read of a stopped hub = %v, want an error that is no *hub.Error", err)
	}
}

func TestWrite_SendsTheDocumentAgainstItsRevision(t *testing.T) {
	t.Parallel()
	h, c, _ := newHub(t)
	h.SetPolicy("demo", stored())
	doc := document(t, stored())
	// A rule made in code, its lists left nil, as protect makes one.
	doc.Rules = slices.Insert(doc.Rules, 0, policy.Rule{Name: "deploy", Paths: []string{"deploy/**"},
		Require: policy.Requirement{Approvals: 1, Approvers: []string{"owner"}}})

	rev, err := policy.Write(t.Context(), c, "demo", doc, 3)
	if err != nil || rev != 4 {
		t.Fatalf("Write = %d, %v; want 4, nil", rev, err)
	}
	doc.Revision = 4
	if got, want := jsonOf(t, document(t, h.Policy("demo"))), jsonOf(t, doc); got != want {
		t.Errorf("the hub holds %s, want %s", got, want)
	}
	puts := h.PolicyPuts("demo")
	if len(puts) != 1 || puts[0].IfMatch != "3" {
		t.Fatalf("PUTs %+v, want one with If-Match 3", puts)
	}
	var body map[string]json.RawMessage
	if err := json.Unmarshal(puts[0].Body, &body); err != nil {
		t.Fatal(err)
	}
	if keys := slices.Sorted(maps.Keys(body)); !slices.Equal(keys, []string{"default", "rules"}) || bytes.Contains(puts[0].Body, []byte("null")) {
		t.Errorf("the PUT's body %s, want only default and rules, with no null", puts[0].Body)
	}
}

func TestWrite_ReportsTheHubsRefusal(t *testing.T) {
	t.Parallel()
	bad := policy.Rule{Name: "bad", Paths: []string{"ok/**", "/etc/**"}}
	tests := []struct {
		name     string
		script   func(h *hubtest.Hub)
		rules    []policy.Rule
		revision int
		check    func(t *testing.T, err error)
	}{
		{"a stale revision", nil, nil, 2, func(t *testing.T, err error) {
			if !errors.Is(err, policy.ErrRevisionMismatch) {
				t.Errorf("err = %v, want ErrRevisionMismatch", err)
			}
		}},
		{"a glob the hub refuses", nil, []policy.Rule{rule("docs", complete()), bad}, 3, func(t *testing.T, err error) {
			var ie *policy.InvalidError
			if !errors.As(err, &ie) || ie.Rule != "bad" || ie.Index != 1 || ie.Field != "paths[1]" || ie.Message == "" {
				t.Errorf("err = %#v, want an InvalidError of rule bad, index 1, field paths[1], with the hub's message", err)
			}
		}},
		{"a scripted refusal", func(h *hubtest.Hub) {
			h.FailNextPolicyPut("demo", http.StatusBadRequest, "invalid_policy", "glob a[b is malformed",
				map[string]any{"rule": "nightshift-1", "index": 0, "field": "paths[0]"})
		}, nil, 3, func(t *testing.T, err error) {
			var ie *policy.InvalidError
			want := policy.InvalidError{Message: "glob a[b is malformed", Rule: "nightshift-1", Index: 0, Field: "paths[0]"}
			if !errors.As(err, &ie) || *ie != want {
				t.Errorf("err = %#v, want %#v", err, want)
			}
		}},
		{"another hub error", func(h *hubtest.Hub) {
			h.FailNextPolicyPut("demo", http.StatusConflict, "workspace_archived", "archived", nil)
		}, nil, 3, func(t *testing.T, err error) {
			var he *hub.Error
			var ie *policy.InvalidError
			if !errors.As(err, &he) || he.Status != http.StatusConflict || errors.As(err, &ie) || errors.Is(err, policy.ErrRevisionMismatch) {
				t.Errorf("err = %v, want the hub's 409 as a *hub.Error", err)
			}
		}},
		{"an unreachable hub", func(h *hubtest.Hub) { h.Stop() }, nil, 3, func(t *testing.T, err error) {
			var he *hub.Error
			if err == nil || errors.As(err, &he) {
				t.Errorf("err = %v, want an error that is no *hub.Error", err)
			}
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h, c, _ := newHub(t)
			h.SetPolicy("demo", stored())
			if tt.script != nil {
				tt.script(h)
			}
			doc := document(t, stored())
			if tt.rules != nil {
				doc.Rules = tt.rules
			}
			rev, err := policy.Write(t.Context(), c, "demo", doc, tt.revision)
			if rev != 0 {
				t.Errorf("Write answered revision %d with %v, want 0", rev, err)
			}
			tt.check(t, err)
			if got := h.Policy("demo").Revision; got != 3 {
				t.Errorf("the hub's policy is at revision %d, want 3", got)
			}
		})
	}
}

func TestNextName_IsOneAboveTheHighestNightshiftNumber(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name  string
		rules []string
		want  string
	}{
		{"no rules", nil, "nightshift-1"},
		{"nightshift-1", []string{"nightshift-1"}, "nightshift-2"},
		{"the highest number counts, other names do not", []string{"nightshift-1", "nightshift-7", "docs"}, "nightshift-8"},
		{"only a decimal number counts, by its value", []string{"nightshift-x", "nightshift-02"}, "nightshift-3"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			doc := policy.Document{Default: complete()}
			for _, name := range tt.rules {
				doc.Rules = append(doc.Rules, rule(name, complete()))
			}
			if got := policy.NextName(doc); got != tt.want {
				t.Errorf("NextName(%v) = %q, want %q", tt.rules, got, tt.want)
			}
		})
	}
}
