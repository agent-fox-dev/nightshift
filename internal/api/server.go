// Package api is nightshiftd's HTTP API: a router that admits only the
// client token, answers every error as JSON, and lets other packages
// register their routes.
package api

import (
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
)

// Server is the API's handler. Its zero value is not usable; call New.
type Server struct {
	token    []byte
	hub      HubReporter
	mux      *http.ServeMux
	errorLog io.Writer

	mu      sync.RWMutex
	sources map[string]StatusSource
}

// Option configures a Server.
type Option func(*Server)

// WithErrorLog writes a line to w for every handler that panicked; without
// it the panic is answered and not logged.
func WithErrorLog(w io.Writer) Option { return func(s *Server) { s.errorLog = w } }

// New returns the API's handler admitting requests that bear token, with
// GET /api/v1/status reporting the hub's state from hub.
func New(token string, hub HubReporter, opts ...Option) *Server {
	s := &Server{
		token:    []byte(token),
		hub:      hub,
		mux:      http.NewServeMux(),
		errorLog: io.Discard,
		sources:  map[string]StatusSource{},
	}
	for _, opt := range opts {
		opt(s)
	}
	s.Handle("GET /api/v1/status", http.HandlerFunc(s.status))
	return s
}

// Handle registers h for pattern, a net/http ServeMux pattern with a method
// ("GET /api/v1/status"). Every route sits behind the client token check.
func (s *Server) Handle(pattern string, h http.Handler) {
	s.mux.Handle(pattern, h)
}

// ServeHTTP checks the client token, then routes the request, answering a
// panic as 500 internal and the router's own 404 and 405 as JSON errors.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// The token is checked before the route, so that a caller without it
	// learns nothing about which routes exist.
	if !s.authorized(r) {
		WriteError(w, http.StatusUnauthorized, "unauthorized", "the client token is missing or wrong")
		return
	}
	defer func() {
		v := recover()
		if v == nil {
			return
		}
		if v == http.ErrAbortHandler {
			panic(v)
		}
		fmt.Fprintf(s.errorLog, "api: %s %s panicked: %v\n", r.Method, r.URL.Path, v)
		WriteError(w, http.StatusInternalServerError, "internal", "the request failed inside nightshiftd")
	}()
	if _, pattern := s.mux.Handler(r); pattern == "" {
		// No route matched: the router answers 404 or 405 in plain text,
		// which is replaced here by the JSON error.
		mw := &missWriter{ResponseWriter: w}
		s.mux.ServeHTTP(mw, r)
		switch mw.status {
		case http.StatusNotFound:
			WriteError(w, http.StatusNotFound, "not_found", "no route for "+r.URL.Path)
		case http.StatusMethodNotAllowed:
			WriteError(w, http.StatusMethodNotAllowed, "method_not_allowed", r.Method+" is not allowed on "+r.URL.Path)
		}
		return
	}
	s.mux.ServeHTTP(w, r)
}

// authorized reports whether r bears the client token, compared in
// constant time.
func (s *Server) authorized(r *http.Request) bool {
	token, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	return ok && len(s.token) > 0 && subtle.ConstantTimeCompare([]byte(token), s.token) == 1
}

// missWriter holds back the router's 404 and 405 answers, so that the
// caller writes them as JSON; any other answer passes through.
type missWriter struct {
	http.ResponseWriter
	status int
}

func (w *missWriter) WriteHeader(status int) {
	if status == http.StatusNotFound || status == http.StatusMethodNotAllowed {
		w.status = status
		return
	}
	w.ResponseWriter.WriteHeader(status)
}

func (w *missWriter) Write(p []byte) (int, error) {
	if w.status != 0 {
		return len(p), nil
	}
	return w.ResponseWriter.Write(p)
}

// WriteJSON answers status with v as the JSON body.
func WriteJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

// WriteError answers status with the error body {"error_type", "message"}.
func WriteError(w http.ResponseWriter, status int, errorType, message string) {
	WriteErrorWith(w, status, errorType, message, nil)
}

// WriteErrorWith answers status with the error body and the members of
// extra beside error_type and message.
func WriteErrorWith(w http.ResponseWriter, status int, errorType, message string, extra map[string]any) {
	body := make(map[string]any, len(extra)+2)
	for k, v := range extra {
		body[k] = v
	}
	body["error_type"] = errorType
	body["message"] = message
	// The router's plain-text error leaves headers that no longer fit.
	w.Header().Del("X-Content-Type-Options")
	WriteJSON(w, status, body)
}
