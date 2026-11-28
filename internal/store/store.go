// Package store is the daemon's SQLite file. Packages register their schema
// migrations with Register; Open applies those not yet applied.
package store

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	// The driver registers itself as "sqlite".
	_ "modernc.org/sqlite"
)

// pragmas are set on every connection: wait for a lock rather than fail
// at once, let readers run beside the writer, and enforce foreign keys.
const pragmas = "?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)&_pragma=foreign_keys(1)"

type component struct {
	name       string
	migrations []string
}

var registry struct {
	sync.Mutex
	components []*component
}

// Register adds migrations to the component's list, numbered on from the
// ones registered before (the first is version 1). A component's versions
// are tracked on their own, so the order in which packages register does
// not matter; a migration, once released, must never change.
func Register(componentName string, migrations ...string) {
	registry.Lock()
	defer registry.Unlock()
	for _, c := range registry.components {
		if c.name == componentName {
			c.migrations = append(c.migrations, migrations...)
			return
		}
	}
	registry.components = append(registry.components, &component{name: componentName, migrations: append([]string(nil), migrations...)})
}

// registered returns a copy of the registry, so that Open does not hold
// its lock while it migrates.
func registered() []component {
	registry.Lock()
	defer registry.Unlock()
	out := make([]component, len(registry.components))
	for i, c := range registry.components {
		out[i] = component{name: c.name, migrations: append([]string(nil), c.migrations...)}
	}
	return out
}

// Store is an open store file.
type Store struct {
	db *sql.DB
}

// Open opens the store file at path, creating the file and its parent
// directory (mode 0700) where they do not exist, and applies every
// registered migration not yet applied, each in its own transaction.
func Open(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	// Without the "file:" prefix the driver takes the path verbatim and
	// strips the query, so no character of the path needs escaping.
	db, err := sql.Open("sqlite", path+pragmas)
	if err != nil {
		return nil, err
	}
	if err := migrate(db); err != nil {
		db.Close()
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return &Store{db: db}, nil
}

func migrate(db *sql.DB) error {
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS schema_migrations (
		component TEXT NOT NULL,
		version INTEGER NOT NULL,
		PRIMARY KEY (component, version)
	)`); err != nil {
		return err
	}
	for _, c := range registered() {
		var applied int
		if err := db.QueryRow(`SELECT COALESCE(MAX(version), 0) FROM schema_migrations WHERE component = ?`, c.name).Scan(&applied); err != nil {
			return err
		}
		for i := applied; i < len(c.migrations); i++ {
			if err := apply(db, c.name, i+1, c.migrations[i]); err != nil {
				return fmt.Errorf("migration %s %d: %w", c.name, i+1, err)
			}
		}
	}
	return nil
}

func apply(db *sql.DB, componentName string, version int, migration string) error {
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.Exec(migration); err != nil {
		return err
	}
	if _, err := tx.Exec(`INSERT INTO schema_migrations (component, version) VALUES (?, ?)`, componentName, version); err != nil {
		return err
	}
	return tx.Commit()
}

// DB is the store's database handle.
func (s *Store) DB() *sql.DB { return s.db }

// Close closes the store; the last connection's close checkpoints the
// write-ahead log into the file.
func (s *Store) Close() error { return s.db.Close() }
