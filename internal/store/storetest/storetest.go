// Package storetest gives tests a migrated store file without migrating
// one per test: the first call in a test process migrates a template with
// every migration registered in the binary, and each call writes a copy.
package storetest

import (
	"os"
	"path/filepath"
	"sync"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/store"
)

var template struct {
	once sync.Once
	data []byte
	err  error
}

// Path returns the path of a fresh copy of the migrated template, in a
// directory of the test's own that is removed with it.
func Path(t testing.TB) string {
	t.Helper()
	template.once.Do(func() { template.data, template.err = build() })
	if template.err != nil {
		t.Fatalf("storetest: building the template: %v", template.err)
	}
	path := filepath.Join(t.TempDir(), "store.db")
	if err := os.WriteFile(path, template.data, 0o600); err != nil {
		t.Fatalf("storetest: %v", err)
	}
	return path
}

// Open opens a fresh copy of the migrated template and closes it when the
// test ends.
func Open(t testing.TB) *store.Store {
	t.Helper()
	s, err := store.Open(Path(t))
	if err != nil {
		t.Fatalf("storetest: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

// build migrates a store in a scratch directory and keeps its bytes, so
// that nothing of the template outlives the process.
func build() ([]byte, error) {
	dir, err := os.MkdirTemp("", "storetest-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	path := filepath.Join(dir, "store.db")
	s, err := store.Open(path)
	if err != nil {
		return nil, err
	}
	if err := s.Close(); err != nil {
		return nil, err
	}
	return os.ReadFile(path)
}
