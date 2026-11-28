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

// Green is the state of a workspace nightshift works in; a registration
// stores it.
const Green State = "green"

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
