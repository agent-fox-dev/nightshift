// Package policy reads a workspace's landing policy on the hub, judges
// whether it counts nightshift's three checks and which of its rules need
// the person, and writes it back.
package policy

import (
	"encoding/json"
	"slices"
	"strconv"
	"strings"
)

// Checks are the checks nightshift reports on a version of a change, in the
// order adopting appends them.
var Checks = []string{"nightshift-gate", "nightshift-review", "nightshift-release"}

// DefaultName is the name the default requirement goes by where rules are
// named.
const DefaultName = "default"

// Document is a landing policy as the hub answers it in normal form.
type Document struct {
	Revision int         `json:"revision"`
	Default  Requirement `json:"default"`
	Rules    []Rule      `json:"rules"`
}

// Rule is one rule of a policy; the first rule a file matches applies.
type Rule struct {
	Name    string   `json:"name"`
	Paths   []string `json:"paths"`
	Exclude []string `json:"exclude"`
	// Targets is nil when the hub sent none, and is then not sent back, so
	// that a rule keeps what the hub held.
	Targets []string    `json:"targets,omitzero"`
	Require Requirement `json:"require"`
}

// Requirement is what a rule, or the default, demands of a change before
// it lands.
type Requirement struct {
	Approvals   int      `json:"approvals"`
	Approvers   []string `json:"approvers"`
	Checks      []string `json:"checks"`
	FreshChecks bool     `json:"fresh_checks"`
	Block       bool     `json:"block"`
}

// MarshalJSON writes every list, an empty one as [], since the hub's normal
// form has every member.
func (r Requirement) MarshalJSON() ([]byte, error) {
	type plain Requirement
	p := plain(r)
	p.Approvers = orEmpty(p.Approvers)
	p.Checks = orEmpty(p.Checks)
	return json.Marshal(p)
}

// MarshalJSON writes paths and exclude, an empty one as [], and targets
// only when the rule has them.
func (r Rule) MarshalJSON() ([]byte, error) {
	type plain Rule
	p := plain(r)
	p.Paths = orEmpty(p.Paths)
	p.Exclude = orEmpty(p.Exclude)
	return json.Marshal(p)
}

func orEmpty(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

// SensitiveRule is a rule, or the default, that makes a change wait for the
// person or refuses it.
type SensitiveRule struct {
	Name      string   `json:"name"`
	Paths     []string `json:"paths"`
	Approvals int      `json:"approvals"`
	Block     bool     `json:"block"`
}

// Missing names the requirements that do not count the three checks on the
// version itself: `default` first, then each rule that does not block, in
// document order. A rule that blocks lets nothing land, so its checks do
// not matter. An empty result means the policy requires the three checks.
func Missing(doc Document) []string {
	var names []string
	if lacks(doc.Default) {
		names = append(names, DefaultName)
	}
	for _, r := range doc.Rules {
		if !r.Require.Block && lacks(r.Require) {
			names = append(names, r.Name)
		}
	}
	return names
}

// lacks reports whether req does not count every one of the three checks
// on the version itself.
func lacks(req Requirement) bool {
	if !req.FreshChecks {
		return true
	}
	for _, c := range Checks {
		if !slices.Contains(req.Checks, c) {
			return true
		}
	}
	return false
}

// Adopt returns a copy of doc in which every requirement Missing names has
// the checks it lacks appended in the order of Checks, after the checks it
// names already, and fresh_checks set. Nothing else changes, and doc is
// left as it was, so that Missing of the result is empty.
func Adopt(doc Document) Document {
	out := doc
	out.Default = adopt(doc.Default)
	if doc.Rules != nil {
		out.Rules = make([]Rule, len(doc.Rules))
		for i, r := range doc.Rules {
			r.Paths = slices.Clone(r.Paths)
			r.Exclude = slices.Clone(r.Exclude)
			r.Targets = slices.Clone(r.Targets)
			if r.Require.Block {
				r.Require = cloneRequirement(r.Require)
			} else {
				r.Require = adopt(r.Require)
			}
			out.Rules[i] = r
		}
	}
	return out
}

// adopt is a copy of req that counts the three checks on the version
// itself.
func adopt(req Requirement) Requirement {
	out := cloneRequirement(req)
	if !lacks(req) {
		return out
	}
	for _, c := range Checks {
		if !slices.Contains(out.Checks, c) {
			out.Checks = append(out.Checks, c)
		}
	}
	out.FreshChecks = true
	return out
}

func cloneRequirement(req Requirement) Requirement {
	req.Approvers = slices.Clone(req.Approvers)
	req.Checks = slices.Clone(req.Checks)
	return req
}

// Sensitive is the rules whose requirement makes a change wait for an
// approval or refuses it, in document order, then `default` when it is
// one; never nil, since the daemon answers it as a list. The default
// applies to every path no rule matches, so it has no paths of its own.
func Sensitive(doc Document) []SensitiveRule {
	out := []SensitiveRule{}
	for _, r := range doc.Rules {
		if sensitive(r.Require) {
			out = append(out, SensitiveRule{Name: r.Name, Paths: orEmpty(slices.Clone(r.Paths)), Approvals: r.Require.Approvals, Block: r.Require.Block})
		}
	}
	if sensitive(doc.Default) {
		out = append(out, SensitiveRule{Name: DefaultName, Paths: []string{}, Approvals: doc.Default.Approvals, Block: doc.Default.Block})
	}
	return out
}

func sensitive(req Requirement) bool { return req.Approvals >= 1 || req.Block }

// namePrefix begins the name of a rule nightshift writes when it is given
// none.
const namePrefix = "nightshift-"

// NextName is the name of a rule nightshift writes when it is given none:
// nightshift-<n>, n one above the highest number among the rules named
// nightshift-<decimal number>, and 1 when there is none. Only such names
// count, by their value, so nightshift-02 counts as 2 and nightshift-x not
// at all; the result is then never a name the document holds.
func NextName(doc Document) string {
	highest := 0
	for _, r := range doc.Rules {
		digits, ok := strings.CutPrefix(r.Name, namePrefix)
		if !ok || digits == "" || strings.Trim(digits, "0123456789") != "" {
			continue
		}
		if n, err := strconv.Atoi(digits); err == nil && n > highest {
			highest = n
		}
	}
	return namePrefix + strconv.Itoa(highest+1)
}
