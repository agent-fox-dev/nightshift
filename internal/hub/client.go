// Package hub is nightshift's client of a Parley hub.
package hub

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Credential names which of nightshift's two hub tokens a request carries.
type Credential int

const (
	// Worker is the token nightshift acts with.
	Worker Credential = iota
	// Approver is the token that approves changes for the person.
	Approver
)

func (c Credential) String() string {
	switch c {
	case Worker:
		return "worker"
	case Approver:
		return "approver"
	}
	return "credential(" + strconv.Itoa(int(c)) + ")"
}

// Error is a hub's answer with a status of 400 or more, decoded from
// Parley's envelope {"error": {"code", "message", "error_type", "details"}}.
// An answer without the envelope leaves the names empty.
type Error struct {
	Status    int
	Code      string
	ErrorType string
	Message   string
	// Details is the envelope's error.details as the hub sent it, or nil;
	// a refused policy names the rule and the field in it.
	Details json.RawMessage
}

// Error is "<status> <name>", the name being the error_type, else the code.
func (e *Error) Error() string {
	name := e.ErrorType
	if name == "" {
		name = e.Code
	}
	if name == "" {
		return strconv.Itoa(e.Status)
	}
	return strconv.Itoa(e.Status) + " " + name
}

// Named reports whether the hub named the error name. Parley's envelope
// carries a name in code and in error_type, and either may hold it.
func (e *Error) Named(name string) bool {
	return e.Code == name || e.ErrorType == name
}

// Token is Parley's Token object.
type Token struct {
	TokenID     string          `json:"token_id"`
	UserID      string          `json:"user_id"`
	Name        string          `json:"name"`
	Permissions []string        `json:"permissions"`
	Paths       json.RawMessage `json:"paths"`
	Workspace   json.RawMessage `json:"workspace"`
	ExpiresAt   *string         `json:"expires_at"`
	RevokedAt   *string         `json:"revoked_at"`
	CreatedAt   string          `json:"created_at"`
}

// Bound reports whether the token is bound to a workspace: its workspace
// member is present and not null.
func (t Token) Bound() bool {
	w := bytes.TrimSpace(t.Workspace)
	return len(w) > 0 && string(w) != "null"
}

// Holds reports whether the token holds scope, by exact name: the hub's own
// rule for minting counts exact names only.
func (t Token) Holds(scope string) bool {
	return slices.Contains(t.Permissions, scope)
}

const patPrefix = "parley_pat_"

// TokenID returns the token id of a personal access token,
// parley_pat_<token_id>_<secret>, and false for any other text. The token id
// runs to the next "_", and the secret, the rest, must not be empty.
func TokenID(pat string) (string, bool) {
	rest, ok := strings.CutPrefix(pat, patPrefix)
	if !ok {
		return "", false
	}
	id, secret, ok := strings.Cut(rest, "_")
	if !ok || id == "" || secret == "" {
		return "", false
	}
	return id, true
}

// Client sends requests to one hub with nightshift's two tokens, and
// reports the outcome of each to its monitor.
type Client struct {
	endpoint string
	http     *http.Client
	// monitor is nil for a client that checks tokens not yet in use, whose
	// outcomes say nothing about the tokens the daemon holds.
	monitor *Monitor

	// mu guards the tokens, which a reload swaps while requests are sent.
	mu       sync.RWMutex
	worker   string
	approver string
}

// Option configures a Client.
type Option func(*Client)

// WithHTTPClient sends the requests through hc.
func WithHTTPClient(hc *http.Client) Option { return func(c *Client) { c.http = hc } }

// New returns a client of the hub at endpoint holding the worker and the
// approver token.
func New(endpoint, worker, approver string, opts ...Option) *Client {
	c := &Client{
		endpoint: strings.TrimRight(endpoint, "/"),
		http:     &http.Client{},
		worker:   worker,
		approver: approver,
	}
	c.monitor = &Monitor{
		ready:    c.Ready,
		clock:    time.Now,
		interval: DefaultProbeInterval,
		wake:     make(chan struct{}, 1),
	}
	for _, opt := range opts {
		opt(c)
	}
	return c
}

// WithTokens returns a client of the same hub holding other tokens, for
// checking tokens before they are taken into use. It has no monitor: a
// refusal of those tokens is not an error of the ones in use.
func (c *Client) WithTokens(worker, approver string) *Client {
	return &Client{endpoint: c.endpoint, http: c.http, worker: worker, approver: approver}
}

// SetTokens takes worker and approver into use for every later request and
// clears the credential errors, which were the previous tokens'.
func (c *Client) SetTokens(worker, approver string) {
	// Under the lock, so that a 401 to the previous tokens that Do is
	// recording cannot land after the errors are cleared.
	c.mu.Lock()
	defer c.mu.Unlock()
	c.worker, c.approver = worker, approver
	if c.monitor != nil {
		c.monitor.clearCredentials()
	}
}

// Endpoint is the hub's endpoint.
func (c *Client) Endpoint() string { return c.endpoint }

// token is the text of cred's token; every request takes it from here.
func (c *Client) token(cred Credential) string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.tokenLocked(cred)
}

// tokenLocked is token for a caller that holds c.mu.
func (c *Client) tokenLocked(cred Credential) string {
	if cred == Approver {
		return c.approver
	}
	return c.worker
}

// NewRequest makes a request for path, which starts with "/", under the
// hub's endpoint.
func (c *Client) NewRequest(ctx context.Context, method, path string, body io.Reader) (*http.Request, error) {
	return http.NewRequestWithContext(ctx, method, c.endpoint+path, body)
}

// Do sends req with the token of cred, under ctx. An answer with a status of
// 400 or more is returned as an *Error, its body read and closed. A request
// that gets no answer marks the hub unreachable, and a 401 records cred's
// token as refused.
func (c *Client) Do(ctx context.Context, cred Credential, req *http.Request) (*http.Response, error) {
	token := c.token(cred)
	req = req.Clone(ctx)
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := c.send(req)
	var he *Error
	// A 401 to a token a reload has replaced meanwhile is not the new
	// token's error; the comparison and the record are made under the lock
	// SetTokens takes, so that a reload cannot fall between them.
	if c.monitor != nil && errors.As(err, &he) && he.Status == http.StatusUnauthorized {
		c.mu.RLock()
		if c.tokenLocked(cred) == token {
			c.monitor.refuse(cred)
		}
		c.mu.RUnlock()
	}
	return resp, err
}

func (c *Client) send(req *http.Request) (*http.Response, error) {
	resp, err := c.http.Do(req)
	if err != nil {
		if c.monitor != nil {
			c.monitor.observe(err)
		}
		return nil, err
	}
	if resp.StatusCode >= 400 {
		defer resp.Body.Close()
		return nil, decodeError(resp)
	}
	return resp, nil
}

func decodeError(resp *http.Response) *Error {
	var envelope struct {
		Error struct {
			Code      string          `json:"code"`
			Message   string          `json:"message"`
			ErrorType string          `json:"error_type"`
			Details   json.RawMessage `json:"details"`
		} `json:"error"`
	}
	// A body that is not the envelope still makes an error with the status.
	_ = json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&envelope)
	return &Error{
		Status:    resp.StatusCode,
		Code:      envelope.Error.Code,
		ErrorType: envelope.Error.ErrorType,
		Message:   envelope.Error.Message,
		Details:   envelope.Error.Details,
	}
}

// Ready asks GET /readyz, which takes no credential. Any answer but 200 is
// an *Error.
func (c *Client) Ready(ctx context.Context) error {
	req, err := c.NewRequest(ctx, http.MethodGet, "/readyz", nil)
	if err != nil {
		return err
	}
	resp, err := c.send(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return &Error{Status: resp.StatusCode}
	}
	return nil
}

// Token reads the Token object tokenID with the token of cred.
func (c *Client) Token(ctx context.Context, cred Credential, tokenID string) (Token, error) {
	req, err := c.NewRequest(ctx, http.MethodGet, "/api/v1/user/tokens/"+url.PathEscape(tokenID), nil)
	if err != nil {
		return Token{}, err
	}
	resp, err := c.Do(ctx, cred, req)
	if err != nil {
		return Token{}, err
	}
	defer resp.Body.Close()
	var tok Token
	if err := json.NewDecoder(resp.Body).Decode(&tok); err != nil {
		return Token{}, fmt.Errorf("decoding the Token object: %w", err)
	}
	return tok, nil
}
