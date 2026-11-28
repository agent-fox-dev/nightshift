package daemon

import (
	"context"
	"errors"
	"fmt"
	"net"
	"syscall"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/config"
	"github.com/agent-fox-dev/nightshift/internal/hub"
)

// defaultHubTimeout is how long the hub start check waits for /readyz.
const defaultHubTimeout = 10 * time.Second

// startCheck is one named check Run runs before it starts anything. Its
// error's text is the reason, which Run prints after the check's name.
type startCheck struct {
	name string
	run  func(*Daemon, context.Context) error
}

// startChecks is the ordered list of start checks. A later check may rely on
// what an earlier one set on the Daemon; a package that adds a check appends
// it here.
func startChecks() []startCheck {
	return []startCheck{
		{"config", (*Daemon).checkConfig},
		{"hub", (*Daemon).checkHub},
		{"credentials", (*Daemon).checkCredentials},
	}
}

// runStartChecks runs the start checks in order and stops at the first that
// fails, printing "<name>: <reason>" for it.
func (d *Daemon) runStartChecks(ctx context.Context) bool {
	for _, c := range startChecks() {
		if err := c.run(d, ctx); err != nil {
			fmt.Fprintf(d.env.Stderr, "%s: %v\n", c.name, err)
			return false
		}
	}
	return true
}

// checkConfig loads the configuration and makes the hub client from it,
// without touching the hub.
func (d *Daemon) checkConfig(context.Context) error {
	cfg, err := config.Load(d.configPath, d.env.Home)
	if err != nil {
		return err
	}
	d.cfg = cfg
	d.hub = hub.New(cfg.Hub.Endpoint, cfg.Hub.WorkerToken, cfg.Hub.ApproverToken,
		hub.WithClock(d.clock()), hub.WithProbeInterval(d.env.ProbeInterval))
	return nil
}

// checkHub asks the hub's /readyz, which needs no credential, so that this
// check does not overlap the credentials check.
func (d *Daemon) checkHub(ctx context.Context) error {
	timeout := d.env.HubTimeout
	if timeout <= 0 {
		timeout = defaultHubTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	return hubReason(d.hub.Ready(ctx))
}

// hubReason turns the outcome of the readiness request into the hub check's
// reason.
func hubReason(err error) error {
	if err == nil {
		return nil
	}
	var he *hub.Error
	var ne net.Error
	switch {
	case errors.As(err, &he):
		return fmt.Errorf("readyz answered %d", he.Status)
	case errors.Is(err, syscall.ECONNREFUSED):
		return errors.New("connection refused")
	case errors.Is(err, context.DeadlineExceeded), errors.As(err, &ne) && ne.Timeout():
		return errors.New("timeout")
	}
	return err
}

func (d *Daemon) checkCredentials(ctx context.Context) error {
	return CheckCredentials(ctx, d.hub, d.cfg.Hub.WorkerToken, d.cfg.Hub.ApproverToken)
}
