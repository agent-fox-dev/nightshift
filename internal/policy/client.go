package policy

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"

	"github.com/agent-fox-dev/nightshift/internal/hub"
)

// ErrRevisionMismatch is a write the hub refused because the policy is no
// longer at the revision the write was based on: someone changed it
// between the read and the write.
var ErrRevisionMismatch = errors.New("the policy changed since it was read")

// InvalidError is a write the hub refused because the document breaks the
// policy format. Rule, Index and Field are the hub's details: the rule's
// name and its index in the rules, and the member that breaks the format,
// such as "name" or "paths[0]".
type InvalidError struct {
	Message string
	Rule    string
	Index   int
	Field   string
}

func (e *InvalidError) Error() string { return "the hub refused the policy: " + e.Message }

func policyPath(slug string) string {
	return "/api/v1/workspaces/" + url.PathEscape(slug) + "/policy"
}

// Read reads the policy of the workspace slug with the worker credential.
// An answer of the hub is a *hub.Error in the returned error; a hub that
// gave no answer is not.
func Read(ctx context.Context, c *hub.Client, slug string) (Document, error) {
	req, err := c.NewRequest(ctx, http.MethodGet, policyPath(slug), nil)
	if err != nil {
		return Document{}, err
	}
	resp, err := c.Do(ctx, hub.Worker, req)
	if err != nil {
		return Document{}, fmt.Errorf("reading the policy of %s: %w", slug, err)
	}
	defer resp.Body.Close()
	var doc Document
	if err := json.NewDecoder(resp.Body).Decode(&doc); err != nil {
		return Document{}, fmt.Errorf("decoding the policy of %s: %w", slug, err)
	}
	return doc, nil
}

// Write writes the default and the rules of doc as the policy of the
// workspace slug with the worker credential, if the policy is still at
// revision, and returns the revision the hub gave it. A 412 is
// ErrRevisionMismatch and a 400 invalid_policy an *InvalidError; any other
// answer of the hub is a *hub.Error in the returned error, and a hub that
// gave no answer is not.
func Write(ctx context.Context, c *hub.Client, slug string, doc Document, revision int) (int, error) {
	rules := doc.Rules
	if rules == nil {
		rules = []Rule{}
	}
	// The revision is the hub's to give, so only what the person may write
	// is sent.
	body, err := json.Marshal(struct {
		Default Requirement `json:"default"`
		Rules   []Rule      `json:"rules"`
	}{doc.Default, rules})
	if err != nil {
		return 0, err
	}
	req, err := c.NewRequest(ctx, http.MethodPut, policyPath(slug), bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("If-Match", strconv.Itoa(revision))
	resp, err := c.Do(ctx, hub.Worker, req)
	if err != nil {
		var he *hub.Error
		if errors.As(err, &he) {
			switch {
			case he.Status == http.StatusPreconditionFailed:
				return 0, fmt.Errorf("writing the policy of %s: %w", slug, ErrRevisionMismatch)
			case he.Status == http.StatusBadRequest && he.Named("invalid_policy"):
				return 0, invalid(he)
			}
		}
		return 0, fmt.Errorf("writing the policy of %s: %w", slug, err)
	}
	defer resp.Body.Close()
	var written struct {
		Revision int `json:"revision"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&written); err != nil {
		return 0, fmt.Errorf("decoding the policy of %s as written: %w", slug, err)
	}
	return written.Revision, nil
}

// invalid is the refusal of a document with the hub's details; details the
// hub did not send, or sent in another shape, are left empty.
func invalid(he *hub.Error) *InvalidError {
	var details struct {
		Rule  string `json:"rule"`
		Index int    `json:"index"`
		Field string `json:"field"`
	}
	_ = json.Unmarshal(he.Details, &details)
	return &InvalidError{Message: he.Message, Rule: details.Rule, Index: details.Index, Field: details.Field}
}
