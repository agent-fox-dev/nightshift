package hub

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// DefaultProbeInterval is how often the monitor asks /readyz while the hub
// is unreachable.
const DefaultProbeInterval = 30 * time.Second

// probeTimeout bounds one /readyz probe, so that a hub that accepts the
// connection and never answers does not hold the probe loop.
const probeTimeout = 10 * time.Second

// Monitor keeps what the client's requests have shown about the hub:
// whether it answers, since when it does not, and which credential it
// stopped accepting. While the hub is unreachable, Run probes /readyz until
// it answers 200 again.
type Monitor struct {
	ready    func(context.Context) error
	clock    func() time.Time
	interval time.Duration
	// wake tells Run that the hub became unreachable.
	wake chan struct{}

	mu sync.Mutex
	// since is the time of the first failure; zero while reachable.
	since time.Time
	// credErr is each credential's recorded error; empty when none.
	credErr [2]string
}

// State is a snapshot of what the monitor knows.
type State struct {
	Reachable bool
	// UnreachableSince is the time of the first failure while the hub is
	// unreachable; zero while it is reachable.
	UnreachableSince time.Time
	// CredentialError is the worker token's recorded error, else the
	// approver token's; empty when there is none.
	CredentialError string
}

// WithClock makes clock the source of the time of a hub's first failure;
// the default is time.Now.
func WithClock(clock func() time.Time) Option {
	return func(c *Client) { c.monitor.clock = clock }
}

// WithProbeInterval makes d the interval of the probes while the hub is
// unreachable; the default is DefaultProbeInterval.
func WithProbeInterval(d time.Duration) Option {
	return func(c *Client) {
		if d > 0 {
			c.monitor.interval = d
		}
	}
}

// Monitor is the client's hub monitor, to which every request it sends
// reports.
func (c *Client) Monitor() *Monitor { return c.monitor }

// Available reports whether the hub can be used with cred's token: it is
// false while the hub is unreachable or while the hub refuses that token.
// It is what the rest of nightshift asks before it relies on the hub.
func (c *Client) Available(cred Credential) bool {
	if c.monitor == nil {
		return true
	}
	return c.monitor.Available(cred)
}

// Reachable reports whether the hub is marked reachable.
func (m *Monitor) Reachable() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.since.IsZero()
}

// Since is the time of the first failure while the hub is unreachable;
// zero while it is reachable.
func (m *Monitor) Since() time.Time {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.since
}

// CredentialError is the worker token's recorded error, else the approver
// token's; empty when the hub refused neither.
func (m *Monitor) CredentialError() string {
	return m.State().CredentialError
}

// State is a consistent snapshot of the monitor.
func (m *Monitor) State() State {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := State{Reachable: m.since.IsZero(), UnreachableSince: m.since}
	for _, e := range m.credErr {
		if e != "" {
			s.CredentialError = e
			break
		}
	}
	return s
}

// Available reports whether the hub is reachable and has not refused
// cred's token.
func (m *Monitor) Available(cred Credential) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.since.IsZero() && m.credErr[credIndex(cred)] == ""
}

// Probe asks /readyz once. A 200 marks the hub reachable and clears the
// time of the first failure; any other answer leaves the state as it is,
// and a connection error or a timeout marks the hub unreachable as any
// request's does.
func (m *Monitor) Probe(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, probeTimeout)
	defer cancel()
	if err := m.ready(ctx); err != nil {
		return err
	}
	m.mu.Lock()
	m.since = time.Time{}
	m.mu.Unlock()
	return nil
}

// Run probes the hub every interval while it is unreachable, sends nothing
// while it is reachable, and returns when ctx is done.
func (m *Monitor) Run(ctx context.Context) {
	for {
		if m.Reachable() {
			select {
			case <-ctx.Done():
				return
			case <-m.wake:
				continue
			}
		}
		t := time.NewTimer(m.interval)
		select {
		case <-ctx.Done():
			t.Stop()
			return
		case <-t.C:
		}
		m.Probe(ctx)
	}
}

// observe takes the outcome of a request that got no answer: a connection
// error or a timeout marks the hub unreachable at the clock's time, unless
// it already is, so that the time stays the first failure's. A request the
// caller cancelled says nothing about the hub.
func (m *Monitor) observe(err error) {
	if err == nil || errors.Is(err, context.Canceled) {
		return
	}
	var he *Error
	if errors.As(err, &he) {
		return
	}
	m.mu.Lock()
	marked := m.since.IsZero()
	if marked {
		m.since = m.clock()
	}
	m.mu.Unlock()
	if marked {
		select {
		case m.wake <- struct{}{}:
		default:
		}
	}
}

// refuse records that the hub answered cred's token 401.
func (m *Monitor) refuse(cred Credential) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.credErr[credIndex(cred)] = fmt.Sprintf("%s token: 401 unauthenticated", cred)
}

// clearCredentials forgets every credential error, for tokens just taken
// into use.
func (m *Monitor) clearCredentials() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.credErr = [2]string{}
}

func credIndex(cred Credential) int {
	if cred == Approver {
		return 1
	}
	return 0
}
