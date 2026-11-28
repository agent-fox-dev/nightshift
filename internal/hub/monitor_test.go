package hub_test

import (
	"context"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
)

var t0 = time.Date(2026, 11, 28, 9, 0, 0, 0, time.UTC)

// testClock is a time the test moves by hand.
type testClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *testClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *testClock) Set(t time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = t
}

// failRequest sends a request with the worker token that the stopped hub
// cannot answer.
func failRequest(t *testing.T, c *hub.Client) {
	t.Helper()
	req, err := c.NewRequest(t.Context(), http.MethodGet, "/api/v1/user/tokens/x", nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := c.Do(t.Context(), hub.Worker, req)
	if err == nil {
		resp.Body.Close()
		t.Fatal("a request to a stopped hub succeeded")
	}
}

// readyzRequests counts the /readyz requests the hub saw.
func readyzRequests(h *hubtest.Hub) int {
	n := 0
	for _, r := range h.Requests() {
		if r.Path == "/readyz" {
			n++
		}
	}
	return n
}

func TestMonitor_ConnectionErrorMarksTheHubUnreachableOnce(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	clock := &testClock{now: t0}
	c := hub.New(h.URL(), worker, approver, hub.WithClock(clock.Now))
	m := c.Monitor()
	h.Stop()

	failRequest(t, c)

	if m.Reachable() {
		t.Error("Reachable() = true after a connection error, want false")
	}
	if got := m.Since(); !got.Equal(t0) {
		t.Errorf("Since() = %v, want the time of the failure %v", got, t0)
	}
	clock.Set(t0.Add(40 * time.Second))
	if err := m.Probe(t.Context()); err == nil {
		t.Error("Probe() of a stopped hub returned no error")
	}
	failRequest(t, c)
	if got := m.Since(); !got.Equal(t0) {
		t.Errorf("Since() = %v after later failures, want the first failure's %v", got, t0)
	}
	for _, cred := range []hub.Credential{hub.Worker, hub.Approver} {
		if c.Available(cred) {
			t.Errorf("Available(%s) = true while the hub is unreachable, want false", cred)
		}
	}
}

func TestMonitor_ProbesUntilTheHubIsReady(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	worker, approver := h.GoodTokens()
	c := hub.New(h.URL(), worker, approver, hub.WithProbeInterval(10*time.Millisecond))
	m := c.Monitor()
	h.Stop()
	failRequest(t, c)
	h.SetReady(http.StatusServiceUnavailable, http.StatusServiceUnavailable, http.StatusServiceUnavailable, http.StatusOK)
	h.Start(t)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		m.Run(ctx)
	}()
	t.Cleanup(func() {
		cancel()
		<-done
	})

	deadline := time.Now().Add(2 * time.Second)
	for !m.Reachable() {
		if time.Now().After(deadline) {
			t.Fatalf("the hub is still unreachable after %d probes", readyzRequests(h))
		}
		time.Sleep(time.Millisecond)
	}
	if n := readyzRequests(h); n != 4 {
		t.Errorf("the hub saw %d /readyz requests, want 4 (three 503, then 200)", n)
	}
	if got := m.Since(); !got.IsZero() {
		t.Errorf("Since() = %v once reachable, want zero", got)
	}
	time.Sleep(50 * time.Millisecond)
	if n := readyzRequests(h); n != 4 {
		t.Errorf("the hub saw %d /readyz requests 50ms after it was reachable, want still 4", n)
	}
	if hub.DefaultProbeInterval != 30*time.Second {
		t.Errorf("DefaultProbeInterval = %v, want 30s", hub.DefaultProbeInterval)
	}
}
