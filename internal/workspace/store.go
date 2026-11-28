package workspace

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/store"
)

func init() {
	// The health timeout is kept in nanoseconds, as a time.Duration counts
	// it, so that what was stored is read back exactly.
	store.Register("workspace",
		`CREATE TABLE workspaces (
			slug TEXT PRIMARY KEY,
			git_url TEXT NOT NULL,
			health_command TEXT NOT NULL,
			health_timeout_ns INTEGER NOT NULL,
			daily_tokens INTEGER NOT NULL,
			state TEXT NOT NULL,
			created_at TEXT NOT NULL
		)`,
		`CREATE TABLE workspace_rules (
			slug TEXT NOT NULL REFERENCES workspaces (slug) ON DELETE CASCADE,
			name TEXT NOT NULL,
			PRIMARY KEY (slug, name)
		)`,
	)
}

const columns = `slug, git_url, health_command, health_timeout_ns, daily_tokens, state, created_at`

type scanner interface{ Scan(dest ...any) error }

func scanWorkspace(row scanner) (Workspace, error) {
	var w Workspace
	var timeout int64
	var created string
	if err := row.Scan(&w.Slug, &w.GitURL, &w.HealthCommand, &timeout, &w.DailyTokens, &w.State, &created); err != nil {
		return Workspace{}, err
	}
	w.HealthTimeout = time.Duration(timeout)
	at, err := time.Parse(time.RFC3339, created)
	if err != nil {
		return Workspace{}, fmt.Errorf("the stored created_at of %s: %w", w.Slug, err)
	}
	w.CreatedAt = at
	return w, nil
}

func (s *Service) get(ctx context.Context, slug string) (Workspace, error) {
	row := s.db.QueryRowContext(ctx, `SELECT `+columns+` FROM workspaces WHERE slug = ?`, slug)
	w, err := scanWorkspace(row)
	if errors.Is(err, sql.ErrNoRows) {
		return Workspace{}, fmt.Errorf("%s: %w", slug, ErrNotFound)
	}
	return w, err
}

func (s *Service) list(ctx context.Context) ([]Workspace, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+columns+` FROM workspaces ORDER BY slug`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Workspace{}
	for rows.Next() {
		w, err := scanWorkspace(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, w)
	}
	return out, rows.Err()
}

func (s *Service) insert(ctx context.Context, w Workspace) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO workspaces (`+columns+`) VALUES (?, ?, ?, ?, ?, ?, ?)`,
		w.Slug, w.GitURL, w.HealthCommand, int64(w.HealthTimeout), w.DailyTokens, string(w.State),
		w.CreatedAt.UTC().Format(time.RFC3339))
	return err
}
