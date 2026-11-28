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

// notFound is ErrNotFound for slug when res changed no row.
func notFound(res sql.Result, slug string) error {
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n == 0 {
		return fmt.Errorf("%s: %w", slug, ErrNotFound)
	}
	return nil
}

func (s *Service) setState(ctx context.Context, slug string, state State) error {
	res, err := s.db.ExecContext(ctx, `UPDATE workspaces SET state = ? WHERE slug = ?`, string(state), slug)
	if err != nil {
		return fmt.Errorf("storing the state of %s: %w", slug, err)
	}
	return notFound(res, slug)
}

// setSettings stores the members of ch that are not nil; a NULL argument
// keeps the column as it is.
func (s *Service) setSettings(ctx context.Context, slug string, ch Settings) error {
	var timeout *int64
	if ch.HealthTimeout != nil {
		ns := int64(*ch.HealthTimeout)
		timeout = &ns
	}
	res, err := s.db.ExecContext(ctx, `UPDATE workspaces SET
			health_command = COALESCE(?, health_command),
			health_timeout_ns = COALESCE(?, health_timeout_ns),
			daily_tokens = COALESCE(?, daily_tokens)
		WHERE slug = ?`, ch.HealthCommand, timeout, ch.DailyTokens, slug)
	if err != nil {
		return fmt.Errorf("storing the settings of %s: %w", slug, err)
	}
	return notFound(res, slug)
}

// delete deletes the workspace slug and its recorded rule names in one
// transaction.
func (s *Service) delete(ctx context.Context, slug string) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `DELETE FROM workspace_rules WHERE slug = ?`, slug); err != nil {
		return fmt.Errorf("removing the rules of %s: %w", slug, err)
	}
	res, err := tx.ExecContext(ctx, `DELETE FROM workspaces WHERE slug = ?`, slug)
	if err != nil {
		return fmt.Errorf("removing %s: %w", slug, err)
	}
	if err := notFound(res, slug); err != nil {
		return err
	}
	return tx.Commit()
}

// recordRule stores name as a rule protect wrote for the workspace slug.
func (s *Service) recordRule(ctx context.Context, slug, name string) error {
	_, err := s.db.ExecContext(ctx, `INSERT OR IGNORE INTO workspace_rules (slug, name) VALUES (?, ?)`, slug, name)
	if err != nil {
		return fmt.Errorf("recording the rule %s of %s: %w", name, slug, err)
	}
	return nil
}

// ruleRecorded reports whether name is recorded as a rule protect wrote for
// the workspace slug.
func (s *Service) ruleRecorded(ctx context.Context, slug, name string) (bool, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM workspace_rules WHERE slug = ? AND name = ?`, slug, name).Scan(&n)
	if err != nil {
		return false, fmt.Errorf("reading the rules of %s: %w", slug, err)
	}
	return n > 0, nil
}

// forgetRule removes the record of the rule name of the workspace slug.
func (s *Service) forgetRule(ctx context.Context, slug, name string) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM workspace_rules WHERE slug = ? AND name = ?`, slug, name)
	if err != nil {
		return fmt.Errorf("forgetting the rule %s of %s: %w", name, slug, err)
	}
	return nil
}
