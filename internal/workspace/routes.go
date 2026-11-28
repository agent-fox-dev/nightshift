package workspace

import (
	"context"
	"encoding/json"
	"errors"
	"maps"
	"net/http"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/api"
	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/policy"
)

// maxBody bounds the body of a request to the workspace routes.
const maxBody = 1 << 20

// Mount registers the workspace routes on srv, each behind its client
// token check, and makes the service the source of the status's
// workspaces.
func (s *Service) Mount(srv *api.Server) {
	srv.Handle("POST /api/v1/workspaces", http.HandlerFunc(s.handleRegister))
	srv.Handle("GET /api/v1/workspaces", http.HandlerFunc(s.handleList))
	srv.Handle("GET /api/v1/workspaces/{slug}", http.HandlerFunc(s.handleShow))
	srv.Handle("PATCH /api/v1/workspaces/{slug}", http.HandlerFunc(s.handleSet))
	srv.Handle("DELETE /api/v1/workspaces/{slug}", http.HandlerFunc(s.handleRemove))
	srv.Handle("POST /api/v1/workspaces/{slug}/policy/rules", http.HandlerFunc(s.handleProtect))
	srv.Handle("DELETE /api/v1/workspaces/{slug}/policy/rules/{name}", http.HandlerFunc(s.handleUnprotect))
	srv.SetStatusSource("workspaces", s.statusWorkspaces)
}

// object is the workspace object the API answers for w, with the members
// the Decorate functions add; a route adds its own members to it.
func (s *Service) object(w Workspace) map[string]any {
	obj := map[string]any{
		"slug":              w.Slug,
		"state":             w.State,
		"health_command":    w.HealthCommand,
		"health_timeout":    w.HealthTimeout.String(),
		"daily_tokens":      w.DailyTokens,
		"tokens_used_today": s.tokensUsedToday(w.Slug),
		"created_at":        w.CreatedAt.UTC().Format(time.RFC3339),
	}
	s.extMu.RLock()
	decorators := slices.Clone(s.decorators)
	s.extMu.RUnlock()
	for _, fn := range decorators {
		fn(w.Slug, obj)
	}
	return obj
}

// statusWorkspaces is the status's workspaces member: the slug, state and
// tokens used today of every workspace, ordered by slug.
func (s *Service) statusWorkspaces(ctx context.Context) (any, error) {
	list, err := s.list(ctx)
	if err != nil {
		return nil, err
	}
	out := make([]map[string]any, 0, len(list))
	for _, w := range list {
		out = append(out, map[string]any{"slug": w.Slug, "state": w.State, "tokens_used_today": s.tokensUsedToday(w.Slug)})
	}
	return out, nil
}

// handleList is GET /api/v1/workspaces; it does not ask the hub.
func (s *Service) handleList(w http.ResponseWriter, r *http.Request) {
	list, err := s.list(r.Context())
	if err != nil {
		writeError(w, r, err)
		return
	}
	objects := make([]map[string]any, 0, len(list))
	for _, ws := range list {
		objects = append(objects, s.object(ws))
	}
	api.WriteJSON(w, http.StatusOK, map[string]any{"workspaces": objects})
}

// handleShow is GET /api/v1/workspaces/{slug}: the workspace object with
// the sensitive rules of the policy as the hub holds it now.
func (s *Service) handleShow(w http.ResponseWriter, r *http.Request) {
	ws, err := s.get(r.Context(), r.PathValue("slug"))
	if err != nil {
		writeError(w, r, err)
		return
	}
	rules, err := s.sensitiveRules(r.Context(), ws.Slug)
	if err != nil {
		writeError(w, r, err)
		return
	}
	obj := s.object(ws)
	obj["sensitive_rules"] = rules
	api.WriteJSON(w, http.StatusOK, obj)
}

// sensitiveRules reads the policy of slug from the hub and gives its
// sensitive rules.
func (s *Service) sensitiveRules(ctx context.Context, slug string) ([]policy.SensitiveRule, error) {
	if !s.hub.Available(hub.Worker) {
		return nil, ErrHubUnavailable
	}
	doc, err := policy.Read(ctx, s.hub, slug)
	if err != nil {
		return nil, s.hubFailure(ctx, err)
	}
	return policy.Sensitive(doc), nil
}

// handleSet is PATCH /api/v1/workspaces/{slug}.
func (s *Service) handleSet(w http.ResponseWriter, r *http.Request) {
	ch, err := decodeSettings(w, r)
	if err != nil {
		writeError(w, r, err)
		return
	}
	ws, err := s.Set(r.Context(), r.PathValue("slug"), ch)
	if err != nil {
		writeError(w, r, err)
		return
	}
	api.WriteJSON(w, http.StatusOK, s.object(ws))
}

// settingMembers are the members a PATCH of a workspace may carry.
var settingMembers = []string{"health_command", "health_timeout", "daily_tokens"}

func decodeSettings(w http.ResponseWriter, r *http.Request) (Settings, error) {
	members, err := decodeObject(w, r)
	if err != nil {
		return Settings{}, err
	}
	for _, name := range slices.Sorted(maps.Keys(members)) {
		if !slices.Contains(settingMembers, name) {
			return Settings{}, &invalidRequest{"unknown member " + strconv.Quote(name) + "; give health_command, health_timeout or daily_tokens"}
		}
	}
	var ch Settings
	if _, ok := members["health_command"]; ok {
		command, err := nonEmptyString(members, "health_command")
		if err != nil {
			return Settings{}, err
		}
		ch.HealthCommand = &command
	}
	if raw, ok := members["health_timeout"]; ok {
		timeout, err := parseHealthTimeout(raw)
		if err != nil {
			return Settings{}, err
		}
		ch.HealthTimeout = &timeout
	}
	if raw, ok := members["daily_tokens"]; ok {
		tokens, err := parseDailyTokens(raw)
		if err != nil {
			return Settings{}, err
		}
		ch.DailyTokens = &tokens
	}
	return ch, nil
}

// handleRemove is DELETE /api/v1/workspaces/{slug}; it does not ask the
// hub.
func (s *Service) handleRemove(w http.ResponseWriter, r *http.Request) {
	if err := s.Remove(r.Context(), r.PathValue("slug")); err != nil {
		writeError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// handleProtect is POST /api/v1/workspaces/{slug}/policy/rules: a rule
// at the top of the hub's policy for the globs of the body.
func (s *Service) handleProtect(w http.ResponseWriter, r *http.Request) {
	members, err := decodeObject(w, r)
	if err != nil {
		writeError(w, r, err)
		return
	}
	var globs []string
	if raw, ok := members["globs"]; !ok || json.Unmarshal(raw, &globs) != nil {
		writeError(w, r, &invalidRequest{"globs must be a list of strings"})
		return
	}
	var block bool
	if raw, ok := members["block"]; ok && json.Unmarshal(raw, &block) != nil {
		writeError(w, r, &invalidRequest{"block must be true or false"})
		return
	}
	var name string
	if raw, ok := members["name"]; ok && json.Unmarshal(raw, &name) != nil {
		writeError(w, r, &invalidRequest{"name must be a string"})
		return
	}
	out, err := s.Protect(r.Context(), r.PathValue("slug"), globs, block, name)
	if err != nil {
		writeError(w, r, err)
		return
	}
	api.WriteJSON(w, http.StatusCreated, map[string]any{
		"rule":     map[string]any{"name": out.Rule.Name, "paths": out.Rule.Paths, "require": out.Rule.Require},
		"revision": out.Revision,
	})
}

// handleUnprotect is DELETE /api/v1/workspaces/{slug}/policy/rules/{name}.
func (s *Service) handleUnprotect(w http.ResponseWriter, r *http.Request) {
	rev, err := s.Unprotect(r.Context(), r.PathValue("slug"), r.PathValue("name"))
	if err != nil {
		writeError(w, r, err)
		return
	}
	api.WriteJSON(w, http.StatusOK, map[string]any{"revision": rev})
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
// for a body that breaks the format, 400 invalid_glob for a refused glob,
// 404 not_found for the route's slug
// when it is not registered, 409 conflict for a refusal with its output
// where it has one, 503 hub_unavailable without the hub, and 500
// otherwise. Nothing is answered to a client that went away.
func writeError(w http.ResponseWriter, r *http.Request, err error) {
	var invalid *invalidRequest
	var glob *globError
	var refusal *Refusal
	switch {
	case r.Context().Err() != nil:
	case errors.Is(err, ErrNotFound):
		api.WriteError(w, http.StatusNotFound, "not_found", "workspace "+r.PathValue("slug")+" is not registered")
	case errors.As(err, &invalid):
		api.WriteError(w, http.StatusBadRequest, "invalid_request", invalid.message)
	case errors.As(err, &glob):
		api.WriteError(w, http.StatusBadRequest, "invalid_glob", glob.message)
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
