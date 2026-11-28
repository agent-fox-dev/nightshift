// Package hubtest is a fake Parley hub for tests. It answers GET /readyz as
// a test scripts it, holds personal access tokens with their permissions,
// workspace binding and revocation, answers GET
// /api/v1/user/tokens/{token_id} in the order Parley checks a request,
// serves real git repositories over smart HTTP at /git/<org>/<slug>.git,
// holds each workspace's landing policy behind GET and PUT
// /api/v1/workspaces/{slug}/policy, and keeps a log of every request it saw.
package hubtest

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

// Hang is a /readyz status that never answers: the request is held until
// the client gives up or the hub stops.
const Hang = -1

// Owner is the user every token belongs to unless it names another.
const Owner = "u_owner"

// WorkerScopes are the scopes nightshift's worker token holds.
func WorkerScopes() []string {
	return []string{
		"workspaces:write", "git:write", "git:read", "changes:write", "checks:write",
		"tokens:manage", "forge:write", "sessions:write", "audit:read",
	}
}

// ApproverScopes are the scopes nightshift's approver token holds.
func ApproverScopes() []string {
	return []string{"changes:read", "changes:approve"}
}

// Token is a personal access token the hub knows.
type Token struct {
	// ID and Secret make the token's text, parley_pat_<ID>_<Secret>; AddToken
	// assigns them when empty.
	ID, Secret string
	// UserID is the token's owner; Owner when empty.
	UserID      string
	Name        string
	Permissions []string
	// Workspace binds the token to a workspace when not empty.
	Workspace string
	Revoked   bool
	// ReadsBound lets a bound token read Token objects, which Parley refuses
	// with 403 token_bound; it stands for a hub that reports the binding
	// only in the object's workspace member.
	ReadsBound bool
}

// PAT is the token's text, as a client sends it.
func (t Token) PAT() string { return "parley_pat_" + t.ID + "_" + t.Secret }

// Request is one request the hub saw, with how it answered.
type Request struct {
	Method, Path string
	// Bearer is the credential of an "Authorization: Bearer" header, or empty.
	Bearer string
	// Basic is the password of an "Authorization: Basic" header, or empty;
	// git sends its credential so.
	Basic string
	// Status is the answer's status; 0 while unanswered.
	Status int
	// ErrorType is the error_type of an error answer.
	ErrorType string
}

// Hub is a running fake hub.
type Hub struct {
	addr string

	mu       sync.Mutex
	srv      *http.Server
	ln       net.Listener
	quit     chan struct{}
	ready    []int
	tokens   map[string]*Token // by PAT
	nextID   int
	requests []Request
	repos    map[string]*Repository  // by path, /git/<org>/<slug>.git
	policies map[string]*policyState // by workspace slug
}

// New starts a fake hub on a loopback address and stops it when the test
// ends. /readyz answers 200 until SetReady says otherwise.
func New(t testing.TB) *Hub {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("hubtest: %v", err)
	}
	h := &Hub{addr: ln.Addr().String(), ready: []int{http.StatusOK}, tokens: map[string]*Token{}}
	h.serve(ln)
	t.Cleanup(h.Stop)
	return h
}

// URL is the hub's endpoint, as hub.endpoint names it.
func (h *Hub) URL() string { return "http://" + h.addr }

// Addr is the hub's host:port; it stays the same across Stop and Start.
func (h *Hub) Addr() string { return h.addr }

// Stop closes the listener and every connection. Requests to the hub's
// address are then refused until Start.
func (h *Hub) Stop() {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.srv == nil {
		return
	}
	close(h.quit)
	// Closing the listener here, not only through the server, refuses
	// connections at once even when Serve has not started on it yet.
	h.ln.Close()
	h.srv.Close()
	h.srv, h.ln = nil, nil
}

// Start listens again on the address the hub had, for a hub that comes
// back after an outage.
func (h *Hub) Start(t testing.TB) {
	t.Helper()
	h.mu.Lock()
	running := h.srv != nil
	h.mu.Unlock()
	if running {
		return
	}
	var ln net.Listener
	var err error
	// The port was just released; give the system a moment to let go of it.
	for range 50 {
		if ln, err = net.Listen("tcp", h.addr); err == nil {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err != nil {
		t.Fatalf("hubtest: listening again on %s: %v", h.addr, err)
	}
	h.serve(ln)
}

func (h *Hub) serve(ln net.Listener) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /readyz", h.readyz)
	mux.HandleFunc("GET /api/v1/user/tokens/{token_id}", h.userToken)
	mux.HandleFunc("/git/", h.gitEndpoint)
	mux.HandleFunc("GET /api/v1/workspaces/{slug}/policy", h.getPolicy)
	mux.HandleFunc("PUT /api/v1/workspaces/{slug}/policy", h.putPolicy)
	mux.HandleFunc("/api/v1/", h.unknownRoute)
	srv := &http.Server{Handler: h.logged(mux)}
	h.mu.Lock()
	h.srv, h.ln = srv, ln
	h.quit = make(chan struct{})
	h.mu.Unlock()
	go srv.Serve(ln)
}

// SetReady scripts /readyz: it answers the statuses in order and repeats
// the last one. A status of Hang never answers.
func (h *Hub) SetReady(statuses ...int) {
	if len(statuses) == 0 {
		statuses = []int{http.StatusOK}
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	h.ready = slices.Clone(statuses)
}

// AddToken adds a token to the hub and returns its text.
func (h *Hub) AddToken(tok Token) string {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.nextID++
	if tok.ID == "" {
		tok.ID = fmt.Sprintf("tok%d", h.nextID)
	}
	if tok.Secret == "" {
		tok.Secret = fmt.Sprintf("secret%d", h.nextID)
	}
	if tok.UserID == "" {
		tok.UserID = Owner
	}
	tok.Permissions = slices.Clone(tok.Permissions)
	h.tokens[tok.PAT()] = &tok
	return tok.PAT()
}

// GoodTokens adds a worker and an approver token with the scopes nightshift
// needs, unbound and of one user, and returns their texts.
func (h *Hub) GoodTokens() (worker, approver string) {
	worker = h.AddToken(Token{Name: "worker", Permissions: WorkerScopes()})
	approver = h.AddToken(Token{Name: "approver", Permissions: ApproverScopes()})
	return worker, approver
}

// Revoke revokes the token with the text pat.
func (h *Hub) Revoke(pat string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if tok, ok := h.tokens[pat]; ok {
		tok.Revoked = true
	}
}

// Requests is every request the hub saw, in order.
func (h *Hub) Requests() []Request {
	h.mu.Lock()
	defer h.mu.Unlock()
	return slices.Clone(h.requests)
}

// APIRequests counts the requests under /api/v1.
func (h *Hub) APIRequests() int {
	n := 0
	for _, r := range h.Requests() {
		if r.Path == "/api/v1" || strings.HasPrefix(r.Path, "/api/v1/") {
			n++
		}
	}
	return n
}

// Bearing is every request that carried the credential pat.
func (h *Hub) Bearing(pat string) []Request {
	var out []Request
	for _, r := range h.Requests() {
		if r.Bearer == pat {
			out = append(out, r)
		}
	}
	return out
}

type entryKey struct{}

// logged records each request before it is routed, so that a request whose
// answer never comes is in the log too.
func (h *Hub) logged(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		bearer, _ := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
		_, basic, _ := r.BasicAuth()
		h.mu.Lock()
		h.requests = append(h.requests, Request{Method: r.Method, Path: r.URL.Path, Bearer: bearer, Basic: basic})
		idx := len(h.requests) - 1
		h.mu.Unlock()
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), entryKey{}, idx)))
	})
}

// answer notes the answer in the log before any of it reaches the client,
// so that a client that read the answer finds it logged.
func (h *Hub) answer(w http.ResponseWriter, r *http.Request, status int, errorType string, body any) {
	h.note(r, status, errorType)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(body)
}

// note enters the answer's status and error type in the request's log
// entry.
func (h *Hub) note(r *http.Request, status int, errorType string) {
	if idx, ok := r.Context().Value(entryKey{}).(int); ok {
		h.mu.Lock()
		h.requests[idx].Status = status
		h.requests[idx].ErrorType = errorType
		h.mu.Unlock()
	}
}

// fail answers Parley's error envelope. The error's name is in both code
// and error_type, so that a client reading either finds it.
func (h *Hub) fail(w http.ResponseWriter, r *http.Request, status int, name, message string) {
	h.answer(w, r, status, name, map[string]any{
		"error": map[string]string{"code": name, "message": message, "error_type": name},
	})
}

func (h *Hub) readyz(w http.ResponseWriter, r *http.Request) {
	h.mu.Lock()
	status := h.ready[0]
	if len(h.ready) > 1 {
		h.ready = h.ready[1:]
	}
	quit := h.quit
	h.mu.Unlock()
	if status == Hang {
		select {
		case <-r.Context().Done():
		case <-quit:
		}
		return
	}
	text := "ready"
	if status != http.StatusOK {
		text = "not ready"
	}
	h.answer(w, r, status, "", map[string]string{"status": text})
}

// authenticate finds the request's bearer token, or answers 401.
func (h *Hub) authenticate(w http.ResponseWriter, r *http.Request) (Token, bool) {
	bearer, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	h.mu.Lock()
	tok, known := h.tokens[bearer]
	var copied Token
	if known {
		copied = *tok
	}
	h.mu.Unlock()
	if !ok || !known || copied.Revoked {
		h.fail(w, r, http.StatusUnauthorized, "unauthenticated", "missing, malformed, unknown or revoked credential")
		return Token{}, false
	}
	return copied, true
}

func (h *Hub) userToken(w http.ResponseWriter, r *http.Request) {
	caller, ok := h.authenticate(w, r)
	if !ok {
		return
	}
	if caller.Workspace != "" && !caller.ReadsBound {
		h.fail(w, r, http.StatusForbidden, "token_bound", "a workspace-bound token cannot use this route")
		return
	}
	if !slices.Contains(caller.Permissions, "tokens:read") && !slices.Contains(caller.Permissions, "tokens:manage") {
		h.fail(w, r, http.StatusForbidden, "insufficient_scope", "the token lacks tokens:read")
		return
	}
	id := r.PathValue("token_id")
	h.mu.Lock()
	var found *Token
	for _, tok := range h.tokens {
		if tok.ID == id && tok.UserID == caller.UserID {
			copied := *tok
			found = &copied
			break
		}
	}
	h.mu.Unlock()
	if found == nil {
		h.fail(w, r, http.StatusNotFound, "not_found", "no such token")
		return
	}
	h.answer(w, r, http.StatusOK, "", tokenObject(*found))
}

// unknownRoute answers a route the fake does not have as Parley would: the
// credential is checked before the route.
func (h *Hub) unknownRoute(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.authenticate(w, r); !ok {
		return
	}
	h.fail(w, r, http.StatusNotFound, "not_found", "no such route")
}

func tokenObject(tok Token) map[string]any {
	var workspace any
	if tok.Workspace != "" {
		workspace = map[string]string{"workspace_id": tok.Workspace}
	}
	var revokedAt any
	if tok.Revoked {
		revokedAt = "2026-11-28T00:00:00Z"
	}
	return map[string]any{
		"token_id":    tok.ID,
		"user_id":     tok.UserID,
		"name":        tok.Name,
		"permissions": tok.Permissions,
		"paths":       []string{},
		"workspace":   workspace,
		"expires_at":  nil,
		"revoked_at":  revokedAt,
		"created_at":  "2026-11-28T00:00:00Z",
	}
}
