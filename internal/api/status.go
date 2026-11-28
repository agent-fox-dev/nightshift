package api

import (
	"context"
	"net/http"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/buildinfo"
)

// HubState is what the status reports about the hub.
type HubState struct {
	Endpoint  string
	Reachable bool
	// UnreachableSince is the time of the first failure while the hub is
	// unreachable; zero while it is reachable.
	UnreachableSince time.Time
	// CredentialError is the error of a credential the hub stopped
	// accepting; empty when there is none.
	CredentialError string
}

// HubReporter reports the hub's current state; the hub monitor implements
// it.
type HubReporter interface {
	HubState() HubState
}

// HubReporterFunc is a function that reports the hub's state as a
// HubReporter.
type HubReporterFunc func() HubState

// HubState calls f.
func (f HubReporterFunc) HubState() HubState { return f() }

// StatusSource gives the value of one member of the status, such as the
// running sessions or the workspaces.
type StatusSource func(ctx context.Context) (any, error)

// statusLists are the members the status always holds, as empty lists
// while no package registered a source for them.
var statusLists = []string{"sessions", "workspaces"}

// SetStatusSource makes src the source of the status member named member,
// "sessions" or "workspaces".
func (s *Server) SetStatusSource(member string, src StatusSource) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sources[member] = src
}

type hubBody struct {
	Endpoint         string  `json:"endpoint"`
	Reachable        bool    `json:"reachable"`
	UnreachableSince *string `json:"unreachable_since"`
	CredentialError  *string `json:"credential_error"`
}

func (s *Server) status(w http.ResponseWriter, r *http.Request) {
	state := s.hub.HubState()
	hub := hubBody{Endpoint: state.Endpoint, Reachable: state.Reachable}
	if !state.UnreachableSince.IsZero() {
		since := state.UnreachableSince.UTC().Format(time.RFC3339)
		hub.UnreachableSince = &since
	}
	if state.CredentialError != "" {
		hub.CredentialError = &state.CredentialError
	}
	body := map[string]any{"version": buildinfo.Version, "hub": hub}
	for _, member := range statusLists {
		body[member] = []any{}
	}

	s.mu.RLock()
	sources := make(map[string]StatusSource, len(s.sources))
	for member, src := range s.sources {
		sources[member] = src
	}
	s.mu.RUnlock()
	for member, src := range sources {
		v, err := src(r.Context())
		if err != nil {
			WriteError(w, http.StatusInternalServerError, "internal", "reading the status's "+member+" failed: "+err.Error())
			return
		}
		body[member] = v
	}
	WriteJSON(w, http.StatusOK, body)
}
