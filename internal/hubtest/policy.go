package hubtest

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

// Policy is a workspace's landing policy as the hub holds it, in normal
// form. Every workspace slug has one: a policy never set is at revision 0
// with an empty default and no rules.
type Policy struct {
	Revision int         `json:"revision"`
	Default  Requirement `json:"default"`
	Rules    []Rule      `json:"rules"`
}

// Rule is one rule of a policy.
type Rule struct {
	Name    string   `json:"name"`
	Paths   []string `json:"paths"`
	Exclude []string `json:"exclude"`
	// Targets is nil for a rule without targets, which the hub then does
	// not answer.
	Targets []string    `json:"targets,omitzero"`
	Require Requirement `json:"require"`
}

// Requirement is what a rule, or the default, demands of a change.
type Requirement struct {
	Approvals   int      `json:"approvals"`
	Approvers   []string `json:"approvers"`
	Checks      []string `json:"checks"`
	FreshChecks bool     `json:"fresh_checks"`
	Block       bool     `json:"block"`
}

// PolicyPut is one PUT of a workspace's policy the hub saw, whatever it
// answered.
type PolicyPut struct {
	// IfMatch is the request's If-Match header as sent.
	IfMatch string
	Body    json.RawMessage
}

// policyState is the policy of one workspace with what tests scripted for
// it; it is guarded by Hub.mu.
type policyState struct {
	policy    Policy
	updatedAt string // empty until the policy is first written
	updatedBy string
	onGet     func()
	failNext  *scriptedError
	puts      []PolicyPut
}

type scriptedError struct {
	status        int
	name, message string
	details       any
}

// maxRules is the most rules a policy may hold.
const maxRules = 200

var ruleName = regexp.MustCompile(`^[a-z0-9_-]{1,63}$`)

// policyOf is the policy state of slug, made at revision 0 on first use.
// The caller holds h.mu.
func (h *Hub) policyOf(slug string) *policyState {
	if h.policies == nil {
		h.policies = map[string]*policyState{}
	}
	st, ok := h.policies[slug]
	if !ok {
		st = &policyState{policy: normalize(Policy{})}
		h.policies[slug] = st
	}
	return st
}

// Policy is the policy of the workspace slug, a copy the caller may change.
func (h *Hub) Policy(slug string) Policy {
	h.mu.Lock()
	defer h.mu.Unlock()
	return clonePolicy(h.policyOf(slug).policy)
}

// SetPolicy sets the policy of the workspace slug as given, its revision
// included, with missing members in normal form.
func (h *Hub) SetPolicy(slug string, p Policy) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.policyOf(slug).policy = normalize(clonePolicy(p))
}

// ReplacePolicy replaces the policy of the workspace slug as a person
// would, at the next revision, and returns that revision; p's own revision
// is ignored.
func (h *Hub) ReplacePolicy(slug string, p Policy) int {
	h.mu.Lock()
	defer h.mu.Unlock()
	st := h.policyOf(slug)
	p = normalize(clonePolicy(p))
	p.Revision = st.policy.Revision + 1
	st.policy = p
	return p.Revision
}

// OnPolicyGet runs fn on every GET of the policy of the workspace slug,
// after the answer is taken and before it is sent, so that fn may replace
// the policy between a client's read and its write. A nil fn removes it.
func (h *Hub) OnPolicyGet(slug string, fn func()) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.policyOf(slug).onGet = fn
}

// FailNextPolicyPut makes the next PUT of the policy of slug that passes
// the credential checks answer status with the error name, message and,
// when not nil, details, and change nothing.
func (h *Hub) FailNextPolicyPut(slug string, status int, name, message string, details any) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.policyOf(slug).failNext = &scriptedError{status: status, name: name, message: message, details: details}
}

// PolicyPuts is every PUT of the policy of slug the hub saw, in order.
func (h *Hub) PolicyPuts(slug string) []PolicyPut {
	h.mu.Lock()
	defer h.mu.Unlock()
	return slices.Clone(h.policyOf(slug).puts)
}

func (h *Hub) getPolicy(w http.ResponseWriter, r *http.Request) {
	caller, ok := h.authenticate(w, r)
	if !ok {
		return
	}
	if !slices.Contains(caller.Permissions, "changes:read") && !slices.Contains(caller.Permissions, "changes:write") {
		h.fail(w, r, http.StatusForbidden, "insufficient_scope", "the token lacks changes:read")
		return
	}
	slug := r.PathValue("slug")
	h.mu.Lock()
	st := h.policyOf(slug)
	body := policyBody(st)
	rev := st.policy.Revision
	hook := st.onGet
	h.mu.Unlock()
	if hook != nil {
		hook()
	}
	w.Header().Set("ETag", etag(rev))
	h.answer(w, r, http.StatusOK, "", body)
}

func (h *Hub) putPolicy(w http.ResponseWriter, r *http.Request) {
	slug := r.PathValue("slug")
	raw, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	h.mu.Lock()
	h.policyOf(slug).puts = append(h.policyOf(slug).puts, PolicyPut{IfMatch: r.Header.Get("If-Match"), Body: raw})
	h.mu.Unlock()

	caller, ok := h.authenticate(w, r)
	if !ok {
		return
	}
	if !slices.Contains(caller.Permissions, "workspaces:write") {
		h.fail(w, r, http.StatusForbidden, "insufficient_scope", "the token lacks workspaces:write")
		return
	}
	if caller.UserID != Owner {
		h.fail(w, r, http.StatusForbidden, "forbidden", "only the workspace's owner may change its policy")
		return
	}

	h.mu.Lock()
	st := h.policyOf(slug)
	scripted := st.failNext
	st.failNext = nil
	current := st.policy.Revision
	h.mu.Unlock()
	if scripted != nil {
		h.failWith(w, r, scripted.status, scripted.name, scripted.message, scripted.details)
		return
	}

	ifMatch := r.Header.Get("If-Match")
	if ifMatch == "" {
		h.fail(w, r, http.StatusPreconditionRequired, "precondition_required", "a policy is written with If-Match")
		return
	}
	if rev, err := strconv.Atoi(strings.Trim(ifMatch, `"`)); err != nil || rev != current {
		h.fail(w, r, http.StatusPreconditionFailed, "revision_mismatch",
			fmt.Sprintf("the policy is at revision %d, not %s", current, ifMatch))
		return
	}

	var doc struct {
		Default *Requirement `json:"default"`
		Rules   []Rule       `json:"rules"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		h.failWith(w, r, http.StatusBadRequest, "invalid_policy", "the body is not a policy document: "+err.Error(), nil)
		return
	}
	if d := validate(doc.Rules); d != nil {
		h.failWith(w, r, http.StatusBadRequest, "invalid_policy", d.message,
			map[string]any{"rule": d.rule, "index": d.index, "field": d.field})
		return
	}
	next := Policy{Rules: doc.Rules}
	if doc.Default != nil {
		next.Default = *doc.Default
	}

	h.mu.Lock()
	st = h.policyOf(slug)
	if st.policy.Revision != current {
		h.mu.Unlock()
		h.fail(w, r, http.StatusPreconditionFailed, "revision_mismatch", "the policy changed meanwhile")
		return
	}
	next = normalize(next)
	next.Revision = current + 1
	st.policy = next
	st.updatedAt = time.Now().UTC().Format(time.RFC3339)
	st.updatedBy = caller.UserID
	body := policyBody(st)
	h.mu.Unlock()
	w.Header().Set("ETag", etag(next.Revision))
	h.answer(w, r, http.StatusOK, "", body)
}

// failWith answers Parley's error envelope with details.
func (h *Hub) failWith(w http.ResponseWriter, r *http.Request, status int, name, message string, details any) {
	envelope := map[string]any{"code": name, "message": message, "error_type": name}
	if details != nil {
		envelope["details"] = details
	}
	h.answer(w, r, status, name, map[string]any{"error": envelope})
}

type refusal struct {
	rule    string
	index   int
	field   string
	message string
}

// validate checks the rules against the policy format, as Parley does,
// and names the first rule and field that break it.
func validate(rules []Rule) *refusal {
	if len(rules) > maxRules {
		return &refusal{index: maxRules, field: "rules", message: fmt.Sprintf("a policy has at most %d rules", maxRules)}
	}
	seen := map[string]bool{}
	for i, rule := range rules {
		refuse := func(field, message string) *refusal {
			return &refusal{rule: rule.Name, index: i, field: field, message: fmt.Sprintf("rule %q: %s: %s", rule.Name, field, message)}
		}
		switch {
		case rule.Name == "default":
			return refuse("name", "default is the default requirement's name")
		case !ruleName.MatchString(rule.Name):
			return refuse("name", "a name is 1 to 63 lower-case letters, digits, _ and -")
		case seen[rule.Name]:
			return refuse("name", "the name is taken by an earlier rule")
		}
		seen[rule.Name] = true
		for _, list := range []struct {
			field string
			globs []string
		}{{"paths", rule.Paths}, {"exclude", rule.Exclude}, {"targets", rule.Targets}} {
			for j, g := range list.globs {
				if len(g) < 1 || len(g) > 255 || strings.HasPrefix(g, "/") {
					return refuse(fmt.Sprintf("%s[%d]", list.field, j), "a glob is 1 to 255 characters and does not start with /")
				}
			}
		}
	}
	return nil
}

// policyBody is the policy as the hub answers it. The caller holds h.mu.
func policyBody(st *policyState) map[string]any {
	p := clonePolicy(st.policy)
	var at, by any
	if st.updatedAt != "" {
		at, by = st.updatedAt, st.updatedBy
	}
	return map[string]any{
		"revision": p.Revision, "default": p.Default, "rules": p.Rules,
		"updated_at": at, "updated_by": by,
	}
}

func etag(revision int) string { return `"` + strconv.Itoa(revision) + `"` }

// normalize gives every member of p a value, a list being [] rather than
// absent; a rule's targets stay absent when it has none.
func normalize(p Policy) Policy {
	p.Default = normalizeRequirement(p.Default)
	if p.Rules == nil {
		p.Rules = []Rule{}
	}
	for i := range p.Rules {
		r := &p.Rules[i]
		r.Paths = orEmpty(r.Paths)
		r.Exclude = orEmpty(r.Exclude)
		r.Require = normalizeRequirement(r.Require)
	}
	return p
}

func normalizeRequirement(req Requirement) Requirement {
	req.Approvers = orEmpty(req.Approvers)
	req.Checks = orEmpty(req.Checks)
	return req
}

func orEmpty(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

func clonePolicy(p Policy) Policy {
	p.Default = cloneRequirement(p.Default)
	if p.Rules != nil {
		rules := make([]Rule, len(p.Rules))
		for i, r := range p.Rules {
			r.Paths = slices.Clone(r.Paths)
			r.Exclude = slices.Clone(r.Exclude)
			r.Targets = slices.Clone(r.Targets)
			r.Require = cloneRequirement(r.Require)
			rules[i] = r
		}
		p.Rules = rules
	}
	return p
}

func cloneRequirement(req Requirement) Requirement {
	req.Approvers = slices.Clone(req.Approvers)
	req.Checks = slices.Clone(req.Checks)
	return req
}
