package workspace

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/api"
)

// maxBody bounds the body of a request to the workspace routes.
const maxBody = 1 << 20

// Mount registers the workspace routes on srv, each behind its client
// token check.
func (s *Service) Mount(srv *api.Server) {
	srv.Handle("POST /api/v1/workspaces", http.HandlerFunc(s.handleRegister))
}

// object is the workspace object the API answers for w; a route adds its
// own members to it.
func (s *Service) object(w Workspace) map[string]any {
	return map[string]any{
		"slug":              w.Slug,
		"state":             w.State,
		"health_command":    w.HealthCommand,
		"health_timeout":    w.HealthTimeout.String(),
		"daily_tokens":      w.DailyTokens,
		"tokens_used_today": int64(0),
		"created_at":        w.CreatedAt.UTC().Format(time.RFC3339),
	}
}

// handleRegister is POST /api/v1/workspaces. The request stays open until
// the registration is decided; a client that goes away ends it, and its
// health command with it.
func (s *Service) handleRegister(w http.ResponseWriter, r *http.Request) {
	reg, err := decodeRegistration(w, r)
	if err != nil {
		writeError(w, r, err)
		return
	}
	out, err := s.Register(r.Context(), reg)
	if err != nil {
		writeError(w, r, err)
		return
	}
	obj := s.object(out.Workspace)
	obj["policy_revision"] = out.PolicyRevision
	obj["warning"] = nil
	if out.Warning != "" {
		obj["warning"] = out.Warning
	}
	api.WriteJSON(w, http.StatusCreated, obj)
}

// invalidRequest is a request whose body breaks its route's format.
type invalidRequest struct{ message string }

func (e *invalidRequest) Error() string { return e.message }

func decodeRegistration(w http.ResponseWriter, r *http.Request) (Registration, error) {
	members, err := decodeObject(w, r)
	if err != nil {
		return Registration{}, err
	}
	var reg Registration
	if reg.Slug, err = nonEmptyString(members, "slug"); err != nil {
		return Registration{}, err
	}
	// The slug names the clone's directory, so it must be one name.
	if reg.Slug == "." || reg.Slug == ".." || strings.ContainsAny(reg.Slug, `/\`) {
		return Registration{}, &invalidRequest{"slug must be a workspace's slug"}
	}
	if reg.HealthCommand, err = nonEmptyString(members, "health_command"); err != nil {
		return Registration{}, err
	}
	if raw, ok := members["health_timeout"]; ok {
		if reg.HealthTimeout, err = parseHealthTimeout(raw); err != nil {
			return Registration{}, err
		}
	}
	if raw, ok := members["daily_tokens"]; ok {
		if reg.DailyTokens, err = parseDailyTokens(raw); err != nil {
			return Registration{}, err
		}
	}
	if raw, ok := members["adopt_policy"]; ok {
		if json.Unmarshal(raw, &reg.AdoptPolicy) != nil {
			return Registration{}, &invalidRequest{"adopt_policy must be true or false"}
		}
	}
	return reg, nil
}

// decodeObject decodes the request's body, which must be a JSON object, into
// its members.
func decodeObject(w http.ResponseWriter, r *http.Request) (map[string]json.RawMessage, error) {
	var members map[string]json.RawMessage
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxBody)).Decode(&members); err != nil || members == nil {
		return nil, &invalidRequest{"the body must be a JSON object"}
	}
	return members, nil
}

func nonEmptyString(members map[string]json.RawMessage, name string) (string, error) {
	var v string
	if raw, ok := members[name]; !ok || json.Unmarshal(raw, &v) != nil || v == "" {
		return "", &invalidRequest{name + " must be a string that is not empty"}
	}
	return v, nil
}

// parseHealthTimeout is a health_timeout member: a Go duration greater
// than zero.
func parseHealthTimeout(raw json.RawMessage) (time.Duration, error) {
	var text string
	if json.Unmarshal(raw, &text) == nil {
		if d, err := time.ParseDuration(text); err == nil && d > 0 {
			return d, nil
		}
	}
	return 0, &invalidRequest{"health_timeout must be a Go duration greater than zero, such as 30m"}
}

// parseDailyTokens is a daily_tokens member: an integer of 1 or more. A
// number with a fraction does not decode into an int64, so it is refused.
func parseDailyTokens(raw json.RawMessage) (int64, error) {
	var n int64
	if json.Unmarshal(raw, &n) != nil || n < 1 {
		return 0, &invalidRequest{"daily_tokens must be an integer of 1 or more"}
	}
	return n, nil
}

// writeError answers err as the workspace routes do: 400 invalid_request
// for a body that breaks the format, 409 conflict for a refusal with its
// output where it has one, 503 hub_unavailable without the hub, and 500
// otherwise. Nothing is answered to a client that went away.
func writeError(w http.ResponseWriter, r *http.Request, err error) {
	var invalid *invalidRequest
	var refusal *Refusal
	switch {
	case r.Context().Err() != nil:
	case errors.As(err, &invalid):
		api.WriteError(w, http.StatusBadRequest, "invalid_request", invalid.message)
	case errors.As(err, &refusal):
		var extra map[string]any
		if refusal.Output != nil {
			extra = map[string]any{"output": refusal.Output}
		}
		api.WriteErrorWith(w, http.StatusConflict, "conflict", refusal.Message, extra)
	case errors.Is(err, ErrHubUnavailable):
		api.WriteError(w, http.StatusServiceUnavailable, "hub_unavailable", err.Error())
	default:
		api.WriteError(w, http.StatusInternalServerError, "internal", err.Error())
	}
}
