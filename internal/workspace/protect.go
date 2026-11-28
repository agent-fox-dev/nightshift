package workspace

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/policy"
)

// maxGlob is the longest path glob the hub's policy format accepts.
const maxGlob = 255

// ruleName is the format the hub's policy gives a rule's name; "default"
// is refused besides, since it names the default requirement.
var ruleName = regexp.MustCompile(`^[a-z0-9_-]{1,63}$`)

// changedMeanwhile is the refusal of a write the hub refused because the
// policy changed after it was read.
const changedMeanwhile = "policy changed meanwhile; run the command again"

// globError is a protection refused for its globs, by the daemon before it
// asks the hub or by the hub's policy format; the route answers it as
// invalid_glob.
type globError struct{ message string }

func (e *globError) Error() string { return e.message }

// refuseGlob is the refusal of the glob g for reason.
func refuseGlob(g, reason string) *globError {
	return &globError{fmt.Sprintf("glob %q is refused: %s", g, reason)}
}

// Protected is a protection's outcome: the rule as written and the
// revision the hub gave the policy.
type Protected struct {
	Rule     policy.Rule
	Revision int
}

// Protect writes a rule for globs at the top of the policy of the
// registered workspace slug, so that it applies before every other rule:
// with block, a requirement that refuses every change; otherwise one that
// waits for an approval of the owner and counts nightshift's three checks
// on the version itself. The rule is named name, or with policy.NextName
// when name is empty, and the name is recorded once the hub accepted the
// write, so that Unprotect removes only the rules Protect wrote.
//
// A glob that is empty, longer than 255 characters or starts with "/", or
// no glob at all, is a glob refusal and a name outside the hub's format a
// request error, both before the hub is asked; a name the policy holds
// already is a *Refusal. A policy changed between the read and the write
// is the *Refusal "policy changed meanwhile; run the command again", and
// the write is not sent again. A glob the hub's format refuses is a glob
// refusal with the hub's message as the reason. ErrHubUnavailable is
// returned when the hub cannot be asked, and an error wrapping ErrNotFound
// for a slug that is not registered.
func (s *Service) Protect(ctx context.Context, slug string, globs []string, block bool, name string) (Protected, error) {
	defer s.rules.lock(slug)()
	if _, err := s.get(ctx, slug); err != nil {
		return Protected{}, err
	}
	if err := checkGlobs(globs); err != nil {
		return Protected{}, err
	}
	if name != "" && (name == policy.DefaultName || !ruleName.MatchString(name)) {
		return Protected{}, &invalidRequest{fmt.Sprintf("rule name %q is refused: a name is 1 to 63 lower-case letters, digits, _ and -, and not default", name)}
	}
	if !s.hub.Available(hub.Worker) {
		return Protected{}, ErrHubUnavailable
	}

	doc, err := s.readPolicy(ctx, slug)
	if err != nil {
		return Protected{}, s.hubFailure(ctx, err)
	}
	if name == "" {
		name = policy.NextName(doc)
	} else if slices.ContainsFunc(doc.Rules, func(r policy.Rule) bool { return r.Name == name }) {
		return Protected{}, refuse("rule %s exists already", name)
	}
	rule := policy.Rule{Name: name, Paths: slices.Clone(globs), Exclude: []string{}, Require: protection(block)}
	doc.Rules = append([]policy.Rule{rule}, doc.Rules...)

	// The write and the record of the rule's name run to their end even
	// when the client goes away meanwhile: a rule the hub took and nightshift
	// did not record could never be unprotected. HubTimeout still bounds the
	// write.
	ctx = context.WithoutCancel(ctx)
	rev, err := s.writePolicy(ctx, slug, doc)
	if err != nil {
		var invalid *policy.InvalidError
		if errors.As(err, &invalid) && strings.HasPrefix(invalid.Field, "paths") {
			return Protected{}, refuseGlob(refusedGlob(globs, invalid), invalid.Message)
		}
		return Protected{}, s.writeFailure(ctx, err)
	}
	if err := s.recordRule(ctx, slug, name); err != nil {
		return Protected{}, err
	}
	return Protected{Rule: rule, Revision: rev}, nil
}

// protection is the requirement of a rule Protect writes.
func protection(block bool) policy.Requirement {
	if block {
		return policy.Requirement{Approvers: []string{"owner"}, Checks: []string{}, Block: true}
	}
	return policy.Requirement{Approvals: 1, Approvers: []string{"owner"}, Checks: slices.Clone(policy.Checks), FreshChecks: true}
}

// checkGlobs refuses what the hub's policy format would refuse of a path
// glob, so that such a protection sends no write.
func checkGlobs(globs []string) error {
	if len(globs) == 0 {
		return &globError{"no glob given; give one or more"}
	}
	for _, g := range globs {
		switch {
		case g == "":
			return refuseGlob(g, "a glob is not empty")
		case len(g) > maxGlob:
			return refuseGlob(g, "a glob is at most 255 characters")
		case strings.HasPrefix(g, "/"):
			return refuseGlob(g, "a glob does not start with /")
		}
	}
	return nil
}

// refusedGlob is the glob of the protection the hub's details name: the
// field paths[<i>] of the new rule, which is the first. A field that names
// no glob of it falls back to the field's own text.
func refusedGlob(globs []string, invalid *policy.InvalidError) string {
	digits, ok := strings.CutPrefix(invalid.Field, "paths[")
	if !ok || invalid.Index != 0 {
		return invalid.Field
	}
	i, err := strconv.Atoi(strings.TrimSuffix(digits, "]"))
	if err != nil || i < 0 || i >= len(globs) {
		return invalid.Field
	}
	return globs[i]
}

// writeFailure is the error of a policy write the hub refused or did not
// answer: the refusal for a policy changed since it was read, a request
// error with the hub's message for a document its format refuses, and
// hubFailure otherwise.
func (s *Service) writeFailure(ctx context.Context, err error) error {
	var invalid *policy.InvalidError
	switch {
	case errors.Is(err, policy.ErrRevisionMismatch):
		return refuse(changedMeanwhile)
	case errors.As(err, &invalid):
		return &invalidRequest{invalid.Message}
	}
	return s.hubFailure(ctx, err)
}

// Unprotect removes the rule name, which Protect wrote, from the policy of
// the registered workspace slug, and forgets its record; it returns the
// policy's revision after the request. A rule the policy no longer holds
// is forgotten and nothing is written. A name Protect did not record for
// slug is the *Refusal "not a rule nightshift wrote", before the hub is
// asked, so that a rule the person wrote is never removed. The other
// errors are those of Protect.
func (s *Service) Unprotect(ctx context.Context, slug, name string) (int, error) {
	defer s.rules.lock(slug)()
	if _, err := s.get(ctx, slug); err != nil {
		return 0, err
	}
	recorded, err := s.ruleRecorded(ctx, slug, name)
	if err != nil {
		return 0, err
	}
	if !recorded {
		return 0, refuse("not a rule nightshift wrote")
	}
	if !s.hub.Available(hub.Worker) {
		return 0, ErrHubUnavailable
	}

	doc, err := s.readPolicy(ctx, slug)
	if err != nil {
		return 0, s.hubFailure(ctx, err)
	}
	i := slices.IndexFunc(doc.Rules, func(r policy.Rule) bool { return r.Name == name })
	rev := doc.Revision
	if i >= 0 {
		doc.Rules = slices.Delete(doc.Rules, i, i+1)
		if rev, err = s.writePolicy(ctx, slug, doc); err != nil {
			return 0, s.writeFailure(ctx, err)
		}
	}
	if err := s.forgetRule(ctx, slug, name); err != nil {
		return 0, err
	}
	return rev, nil
}
