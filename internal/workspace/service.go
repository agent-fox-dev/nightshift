// Package workspace holds the workspaces registered with nightshift: it
// registers a workspace after its five checks, keeps each workspace's
// settings and state in the store, keeps its clone under the clones
// directory, and runs its health command there.
package workspace

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/command"
	"github.com/agent-fox-dev/nightshift/internal/gitclone"
	"github.com/agent-fox-dev/nightshift/internal/hub"
	"github.com/agent-fox-dev/nightshift/internal/store"
)

// ErrNotFound is the error for a slug that is not registered.
var ErrNotFound = errors.New("workspace not registered")

// State is a workspace's stored state.
type State string

// The states of a workspace. A registration stores Green; the packages that
// run the health command and land changes set the others with SetState.
const (
	// Green is the state of a workspace nightshift works in.
	Green State = "green"
	// Red is the state of a workspace whose last health command failed.
	Red State = "red"
	// Held is the state of a workspace with an open decision about its
	// origin or its policy.
	Held State = "held"
)

// valid reports whether st is one of the states.
func (st State) valid() bool { return st == Green || st == Red || st == Held }

// DefaultHealthTimeout is the health timeout a registration stores when it
// names none.
const DefaultHealthTimeout = 30 * time.Minute

// defaultDailyTokens is DefaultDailyTokens of a new service.
const defaultDailyTokens = 50_000_000

// Workspace is a registered workspace as the store holds it.
type Workspace struct {
	Slug string
	// GitURL is the repository's remote, the hub_url the hub gave at the
	// registration.
	GitURL        string
	HealthCommand string
	HealthTimeout time.Duration
	// DailyTokens is the workspace's daily token limit.
	DailyTokens int64
	State       State
	CreatedAt   time.Time
}

// Service holds the registered workspaces. Build it with New.
type Service struct {
	// DefaultDailyTokens is the daily limit a registration stores when it
	// names none, as it is when the registration runs. Another package may
	// set it before the daemon serves.
	DefaultDailyTokens int64

	db        *sql.DB
	hub       *hub.Client
	runner    *command.Runner
	clonesDir string
	now       func() time.Time

	// mu guards registering, the slugs whose registration is running.
	mu          sync.Mutex
	registering map[string]bool

	// extMu guards what other packages register: the source of the tokens
	// used today, the decorations of the workspace object, and the guards
	// and hooks of a removal.
	extMu      sync.RWMutex
	tokensUsed func(slug string) int64
	decorators []func(slug string, obj map[string]any)
	guards     []func(ctx context.Context, slug string) error
	removed    []func(ctx context.Context, slug string)
}

// Settings are the settings of a workspace Set changes; a nil member is
// left as it is stored.
type Settings struct {
	HealthCommand *string
	HealthTimeout *time.Duration
	DailyTokens   *int64
}

// New returns the service of the workspaces in st, which asks the hub
// through hc, runs commands with runner and keeps each workspace's clone
// in clonesDir/<slug>.
func New(st *store.Store, hc *hub.Client, runner *command.Runner, clonesDir string) *Service {
	return &Service{
		DefaultDailyTokens: defaultDailyTokens,
		db:                 st.DB(),
		hub:                hc,
		runner:             runner,
		clonesDir:          clonesDir,
		now:                time.Now,
		registering:        map[string]bool{},
	}
}

// Get returns the registered workspace slug, or an error wrapping
// ErrNotFound.
func (s *Service) Get(ctx context.Context, slug string) (Workspace, error) {
	return s.get(ctx, slug)
}

// List returns every registered workspace, ordered by slug.
func (s *Service) List(ctx context.Context) ([]Workspace, error) {
	return s.list(ctx)
}

// SetState stores state as the state of the registered workspace slug, so
// that every later read reports it. It returns an error naming an unknown
// state, and one wrapping ErrNotFound for a slug that is not registered;
// either changes nothing. It appends no event: the caller writes what the
// change means where it belongs.
func (s *Service) SetState(ctx context.Context, slug string, state State) error {
	if !state.valid() {
		return fmt.Errorf("unknown workspace state %q; want green, red or held", state)
	}
	return s.setState(ctx, slug, state)
}

// Set stores the settings ch gives for the registered workspace slug and
// leaves the others, so that the next run of the health command and the
// next read of the limit use them, and returns the workspace as stored. It
// runs no check and asks the hub nothing. A change that gives nothing, an
// empty health command, a timeout not above zero or a limit below 1 is
// refused; a slug that is not registered is an error wrapping ErrNotFound.
func (s *Service) Set(ctx context.Context, slug string, ch Settings) (Workspace, error) {
	switch {
	case ch.HealthCommand == nil && ch.HealthTimeout == nil && ch.DailyTokens == nil:
		return Workspace{}, &invalidRequest{"give one or more of health_command, health_timeout and daily_tokens"}
	case ch.HealthCommand != nil && *ch.HealthCommand == "":
		return Workspace{}, &invalidRequest{"health_command must be a string that is not empty"}
	case ch.HealthTimeout != nil && *ch.HealthTimeout <= 0:
		return Workspace{}, &invalidRequest{"health_timeout must be a Go duration greater than zero, such as 30m"}
	case ch.DailyTokens != nil && *ch.DailyTokens < 1:
		return Workspace{}, &invalidRequest{"daily_tokens must be an integer of 1 or more"}
	}
	if err := s.setSettings(ctx, slug, ch); err != nil {
		return Workspace{}, err
	}
	return s.get(ctx, slug)
}

// SetTokensUsed makes fn the source of the tokens a workspace used today,
// which the workspace object and the status report; they report 0 while no
// source is set.
func (s *Service) SetTokensUsed(fn func(slug string) int64) {
	s.extMu.Lock()
	defer s.extMu.Unlock()
	s.tokensUsed = fn
}

// tokensUsedToday is the source's count for slug, 0 without a source.
func (s *Service) tokensUsedToday(slug string) int64 {
	s.extMu.RLock()
	fn := s.tokensUsed
	s.extMu.RUnlock()
	if fn == nil {
		return 0
	}
	return fn(slug)
}

// Decorate adds fn to the functions that add members to every workspace
// object the API answers; they are called in the order they were added,
// with the workspace's slug and its object.
func (s *Service) Decorate(fn func(slug string, obj map[string]any)) {
	s.extMu.Lock()
	defer s.extMu.Unlock()
	s.decorators = append(s.decorators, fn)
}

// AddRemoveGuard adds fn to the guards a removal asks, in the order they
// were added; the first that returns an error refuses the removal with
// that error's text.
func (s *Service) AddRemoveGuard(fn func(ctx context.Context, slug string) error) {
	s.extMu.Lock()
	defer s.extMu.Unlock()
	s.guards = append(s.guards, fn)
}

// OnRemoved adds fn to the functions called, in the order they were added,
// with the slug of each workspace once it is removed.
func (s *Service) OnRemoved(fn func(ctx context.Context, slug string)) {
	s.extMu.Lock()
	defer s.extMu.Unlock()
	s.removed = append(s.removed, fn)
}

// Remove removes the registered workspace slug from nightshift, asking the
// hub nothing: once every guard returned no error, it deletes the
// workspace and the names of the rules protect wrote for it, removes its
// clone and calls the OnRemoved functions. The first guard that returns an
// error refuses the removal with a *Refusal of that error's text, and no
// later guard and no OnRemoved function is called. A slug that is not
// registered is an error wrapping ErrNotFound.
func (s *Service) Remove(ctx context.Context, slug string) error {
	if _, err := s.get(ctx, slug); err != nil {
		return err
	}
	s.extMu.RLock()
	guards, removed := slices.Clone(s.guards), slices.Clone(s.removed)
	s.extMu.RUnlock()
	for _, guard := range guards {
		if err := guard(ctx, slug); err != nil {
			return &Refusal{Message: err.Error()}
		}
	}
	if err := s.delete(ctx, slug); err != nil {
		return err
	}
	// The workspace is gone once its row is, so the hooks run even when its
	// clone could not be removed; such a clone is a stray one, which the
	// next start removes.
	cloneErr := os.RemoveAll(s.cloneDir(slug))
	for _, fn := range removed {
		fn(ctx, slug)
	}
	if cloneErr != nil {
		return fmt.Errorf("removing the clone of %s: %w", slug, cloneErr)
	}
	return nil
}

// cloneDir is the directory of the clone of slug.
func (s *Service) cloneDir(slug string) string { return filepath.Join(s.clonesDir, slug) }

// RunHealth brings the clone of the registered workspace slug to commit,
// fetching from the git URL stored at its registration with the worker
// token, and runs the workspace's health command there with its health
// timeout, keeping the last tailLines lines of output. A command that ran
// is a Result whatever its exit; an error is a clone that failed ("cannot
// clone <slug>: …"), a command that could not start ("health command could
// not be started: …"), ctx's error, or one wrapping ErrNotFound.
func (s *Service) RunHealth(ctx context.Context, slug, commit string, tailLines int) (command.Result, error) {
	w, err := s.get(ctx, slug)
	if err != nil {
		return command.Result{}, err
	}
	return s.runHealth(ctx, w, commit, tailLines)
}

// cloneError is a clone that could not be brought to the commit.
type cloneError struct {
	slug string
	err  error
}

func (e *cloneError) Error() string {
	return "cannot clone " + e.slug + ": " + strings.TrimPrefix(e.err.Error(), "gitclone: ")
}

// startError is a health command that could not be started.
type startError struct{ err error }

func (e *startError) Error() string {
	return "health command could not be started: " + strings.TrimPrefix(e.err.Error(), "command: ")
}

func (s *Service) runHealth(ctx context.Context, w Workspace, commit string, tailLines int) (command.Result, error) {
	dir := s.cloneDir(w.Slug)
	err := gitclone.Sync(ctx, gitclone.Options{
		URL:    w.GitURL,
		Token:  s.hub.Secret(hub.Worker),
		Dir:    dir,
		Commit: commit,
		Runner: s.runner,
	})
	if err != nil {
		if ctx.Err() != nil {
			return command.Result{}, ctx.Err()
		}
		return command.Result{}, &cloneError{slug: w.Slug, err: err}
	}
	res, err := s.runner.Run(ctx, command.Spec{
		Dir:       dir,
		Line:      w.HealthCommand,
		Timeout:   w.HealthTimeout,
		TailLines: tailLines,
	})
	if err != nil {
		if ctx.Err() != nil {
			return command.Result{}, ctx.Err()
		}
		return command.Result{}, &startError{err: err}
	}
	return res, nil
}

// RemoveStrayClones removes every directory of the clones directory that is
// not the clone of a registered workspace: what a registration cut off
// before it was decided left behind. A registration running meanwhile
// keeps its clone.
func (s *Service) RemoveStrayClones(ctx context.Context) error {
	entries, err := os.ReadDir(s.clonesDir)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading the clones directory: %w", err)
	}
	list, err := s.list(ctx)
	if err != nil {
		return err
	}
	keep := map[string]bool{}
	for _, w := range list {
		keep[w.Slug] = true
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for slug := range s.registering {
		keep[slug] = true
	}
	var errs []error
	for _, e := range entries {
		if !keep[e.Name()] {
			errs = append(errs, os.RemoveAll(filepath.Join(s.clonesDir, e.Name())))
		}
	}
	return errors.Join(errs...)
}
