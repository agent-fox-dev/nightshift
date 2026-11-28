package daemon

import (
	"context"
	"errors"
	"fmt"
	"net/http"

	"github.com/agent-fox-dev/nightshift/internal/hub"
)

// The scopes each token must hold, in the order a missing one is reported.
// The worker holds git:read too, because the hub lets a token mint only the
// scopes it holds and nightshift mints reviewers' tokens with git:read.
var (
	workerScopes   = []string{"workspaces:write", "git:write", "git:read", "changes:write", "checks:write", "tokens:manage", "forge:write", "sessions:write", "audit:read"}
	approverScopes = []string{"changes:read", "changes:approve"}
)

// CheckCredentials checks the worker and the approver token against the
// hub c talks to, sending these tokens rather than the ones c holds, so that
// tokens can be checked before they are taken into use. The error's text is
// the reason alone, without the name of the start check.
//
// Neither token reaches the hub unless both are personal access tokens and
// they differ. The worker reads both Token objects; the approver holds no
// scope that reads tokens, so its one request on that route proves only
// that the hub accepts it, which any answer but 401 does.
func CheckCredentials(ctx context.Context, c *hub.Client, worker, approver string) error {
	workerID, ok := hub.TokenID(worker)
	if !ok {
		return errors.New("worker token is not a personal access token")
	}
	approverID, ok := hub.TokenID(approver)
	if !ok {
		return errors.New("approver token is not a personal access token")
	}
	if worker == approver {
		return errors.New("worker and approver are the same token")
	}
	c = c.WithTokens(worker, approver)

	own, ownErr := c.Token(ctx, hub.Worker, workerID)
	var ownRefusal *hub.Error
	if ownErr != nil {
		// A bound token and one without a scope that reads tokens are
		// refused with 403; they are reported after the approver is known
		// to be accepted.
		if !errors.As(ownErr, &ownRefusal) || ownRefusal.Status != http.StatusForbidden ||
			!(ownRefusal.Named("token_bound") || ownRefusal.Named("insufficient_scope")) {
			return tokenFailure(hub.Worker, ownErr)
		}
	}

	if _, err := c.Token(ctx, hub.Approver, approverID); err != nil {
		var he *hub.Error
		if !errors.As(err, &he) || he.Status == http.StatusUnauthorized {
			return tokenFailure(hub.Approver, err)
		}
	}

	switch {
	case ownRefusal != nil && ownRefusal.Named("token_bound"), ownRefusal == nil && own.Bound():
		return errors.New("worker token is bound to a workspace")
	case ownRefusal != nil:
		return errors.New("worker token lacks tokens:manage")
	case own.Holds("changes:approve"):
		return errors.New("worker token must not hold changes:approve")
	}
	if scope, missing := firstMissing(own, workerScopes); missing {
		return fmt.Errorf("worker token lacks %s", scope)
	}

	approverTok, err := c.Token(ctx, hub.Worker, approverID)
	if err != nil {
		return tokenFailure(hub.Worker, err)
	}
	if scope, missing := firstMissing(approverTok, approverScopes); missing {
		return fmt.Errorf("approver token lacks %s", scope)
	}
	return nil
}

// tokenFailure is the reason for an answer the check names no other reason
// for, or for a request that got no answer.
func tokenFailure(cred hub.Credential, err error) error {
	var he *hub.Error
	if errors.As(err, &he) && he.Status == http.StatusUnauthorized {
		return fmt.Errorf("%s token: 401 unauthenticated", cred)
	}
	return fmt.Errorf("%s token: %v", cred, err)
}

func firstMissing(tok hub.Token, scopes []string) (string, bool) {
	for _, s := range scopes {
		if !tok.Holds(s) {
			return s, true
		}
	}
	return "", false
}
