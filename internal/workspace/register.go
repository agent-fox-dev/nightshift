package workspace

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/policy"
)

// healthTailLines is how much of a health command's output a refused
// registration reports.
const healthTailLines = 50

// ErrHubUnavailable is the error for a request that needs the hub while
// the hub client reports the worker credential as not available, or while a
// request to the hub gets no answer.
var ErrHubUnavailable = errors.New("the hub is unavailable")

// Refusal is a request this package refuses: a registration whose check
// failed, with the reason in Message and, for a health command that failed
// or timed out, the last lines of its output in Output.
type Refusal struct {
	Message string
	Output  []string
}

func (r *Refusal) Error() string { return r.Message }

func refuse(format string, args ...any) *Refusal {
	return &Refusal{Message: fmt.Sprintf(format, args...)}
}

// Registration is what a registration asks for.
type Registration struct {
	Slug          string
	HealthCommand string
	// HealthTimeout is DefaultHealthTimeout when zero.
	HealthTimeout time.Duration
	// DailyTokens is the service's DefaultDailyTokens when zero.
	DailyTokens int64
	// AdoptPolicy writes nightshift's three checks into a policy that
	// lacks them, instead of refusing it.
	AdoptPolicy bool
}

// Registered is a registration's outcome.
type Registered struct {
	Workspace Workspace
	// PolicyRevision is the revision of the policy as adopted; nil when the
	// policy was not written.
	PolicyRevision *int
	// Warning is the line that tells the person no path is marked
	// sensitive; empty when one is.
	Warning string
}

// Register registers the workspace r.Slug after its five checks, in this
// order: the worker token's user owns it, its sync_enabled is on, the sync
// reports the default branch as following origin, its policy counts
// nightshift's three checks, and its health command exits 0 in a clone at
// the default branch's commit. The first check that fails refuses the
// registration with a *Refusal and nothing is stored. With r.AdoptPolicy a
// policy lacking the checks is adopted after the health command passed,
// the last step before the workspace is stored as green.
//
// A slug that is registered, or whose registration is running, is refused
// without asking the hub. ErrHubUnavailable is returned when the hub
// cannot be asked, and ctx's error when ctx ends, which also ends the
// health command.
func (s *Service) Register(ctx context.Context, r Registration) (Registered, error) {
	if err := s.claim(ctx, r.Slug); err != nil {
		return Registered{}, err
	}
	defer s.release(r.Slug)

	cloned := false
	out, err := s.register(ctx, r, &cloned)
	if err != nil && cloned {
		// A refused registration leaves no clone behind; the daemon's start
		// removes one a crash left.
		os.RemoveAll(s.cloneDir(r.Slug))
	}
	return out, err
}

// claim marks slug's registration as running, or refuses it when one is
// running or the slug is registered.
func (s *Service) claim(ctx context.Context, slug string) error {
	s.mu.Lock()
	if s.registering[slug] {
		s.mu.Unlock()
		return refuse("registration of %s is in progress", slug)
	}
	s.registering[slug] = true
	s.mu.Unlock()

	_, err := s.get(ctx, slug)
	if err == nil {
		s.release(slug)
		return refuse("%s is registered already", slug)
	}
	if !errors.Is(err, ErrNotFound) {
		s.release(slug)
		return err
	}
	return nil
}

func (s *Service) release(slug string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.registering, slug)
}

func (s *Service) register(ctx context.Context, r Registration, cloned *bool) (Registered, error) {
	if !s.hub.Available(hub.Worker) {
		return Registered{}, ErrHubUnavailable
	}
	slug := r.Slug

	ws, err := s.hub.Workspace(ctx, slug)
	var he *hub.Error
	if errors.As(err, &he) && he.Status == http.StatusNotFound {
		return Registered{}, refuse("workspace not found")
	}
	if err != nil {
		return Registered{}, s.hubFailure(ctx, err)
	}
	if ws.Access != "owner" {
		return Registered{}, refuse("not the owner of %s", slug)
	}

	if !ws.Settings.SyncEnabled {
		return Registered{}, refuse("sync is disabled for %s", slug)
	}
	synced, err := s.hub.Sync(ctx, slug)
	if err != nil {
		return Registered{}, s.hubFailure(ctx, err)
	}
	commit, err := followsOrigin(synced.Branches, ws.DefaultBranch)
	if err != nil {
		return Registered{}, err
	}

	doc, err := policy.Read(ctx, s.hub, slug)
	if err != nil {
		return Registered{}, s.hubFailure(ctx, err)
	}
	missing := policy.Missing(doc)
	if len(missing) > 0 && !r.AdoptPolicy {
		return Registered{}, refuse("policy of %s does not require nightshift's checks in: %s; run again with --adopt-policy",
			slug, strings.Join(missing, ", "))
	}

	if ws.HubURL == nil || *ws.HubURL == "" {
		return Registered{}, refuse("cannot clone %s: the hub reports no git URL", slug)
	}
	w := Workspace{
		Slug:          slug,
		GitURL:        *ws.HubURL,
		HealthCommand: r.HealthCommand,
		HealthTimeout: r.HealthTimeout,
		DailyTokens:   r.DailyTokens,
		State:         Green,
	}
	if w.HealthTimeout == 0 {
		w.HealthTimeout = DefaultHealthTimeout
	}
	if w.DailyTokens == 0 {
		w.DailyTokens = s.DefaultDailyTokens
	}
	*cloned = true
	if err := s.checkHealth(ctx, w, commit); err != nil {
		return Registered{}, err
	}

	var out Registered
	if len(missing) > 0 {
		doc = policy.Adopt(doc)
		rev, err := policy.Write(ctx, s.hub, slug, doc, doc.Revision)
		switch {
		case errors.Is(err, policy.ErrRevisionMismatch):
			return Registered{}, refuse("policy changed meanwhile; run the command again")
		case err != nil:
			var invalid *policy.InvalidError
			if errors.As(err, &invalid) {
				return Registered{}, refuse("hub answered 400 invalid_policy: %s", invalid.Message)
			}
			return Registered{}, s.hubFailure(ctx, err)
		}
		doc.Revision = rev
		out.PolicyRevision = &rev
	}
	if len(policy.Sensitive(doc)) == 0 {
		out.Warning = fmt.Sprintf("no path of %s is marked sensitive; every change lands without you", slug)
	}

	w.CreatedAt = s.now().UTC().Truncate(time.Second)
	if err := s.insert(ctx, w); err != nil {
		return Registered{}, fmt.Errorf("storing the workspace %s: %w", slug, err)
	}
	out.Workspace = w
	return out, nil
}

// followsOrigin is the commit of the default branch when the sync reports
// it as following origin, and the refusal otherwise.
func followsOrigin(branches []hub.SyncBranch, defaultBranch string) (string, error) {
	for _, b := range branches {
		if b.Name != defaultBranch {
			continue
		}
		switch b.Result {
		case "up_to_date", "fast_forward", "created":
			return b.LocalSHA, nil
		case "ahead":
			return "", refuse("default branch is ahead of origin")
		case "diverged":
			return "", refuse("default branch has diverged from origin")
		default:
			return "", refuse("default branch: sync reported %s", b.Result)
		}
	}
	return "", refuse("default branch %s is not on origin", defaultBranch)
}

// checkHealth runs the health command of w at commit and refuses the
// registration unless it exits 0.
func (s *Service) checkHealth(ctx context.Context, w Workspace, commit string) error {
	res, err := s.runHealth(ctx, w, commit, healthTailLines)
	var cloneErr *cloneError
	var startErr *startError
	switch {
	case errors.As(err, &cloneErr), errors.As(err, &startErr):
		return &Refusal{Message: err.Error()}
	case err != nil:
		return err
	case res.TimedOut:
		return &Refusal{Message: "health command timed out", Output: orEmpty(res.Tail)}
	case res.ExitCode != 0:
		return &Refusal{Message: "health command exited " + strconv.Itoa(res.ExitCode), Output: orEmpty(res.Tail)}
	}
	return nil
}

func orEmpty(lines []string) []string {
	if lines == nil {
		return []string{}
	}
	return lines
}

// hubFailure is the error of a hub request that failed: ctx's error when
// the request's own context ended, the refusal "hub answered <status>
// <error_type>: <message>" for an answer of the hub, and ErrHubUnavailable
// for a hub that gave no answer.
func (s *Service) hubFailure(ctx context.Context, err error) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	var he *hub.Error
	if errors.As(err, &he) {
		name := he.ErrorType
		if name == "" {
			name = he.Code
		}
		return refuse("hub answered %d %s: %s", he.Status, name, he.Message)
	}
	return fmt.Errorf("%w: %v", ErrHubUnavailable, err)
}
