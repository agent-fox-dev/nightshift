package store_test

import (
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/store"
	"github.com/agent-fox-dev/nightshift/internal/store/storetest"
)

func init() {
	store.Register("store_test", "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
}

func TestOpen_KeepsRowsAcrossReopen(t *testing.T) {
	t.Parallel()
	path := storetest.Path(t)

	s, err := store.Open(path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if _, err := s.DB().Exec(`INSERT INTO notes (body) VALUES ('kept')`); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	// A second Open must not apply the migrations again.
	s, err = store.Open(path)
	if err != nil {
		t.Fatalf("Open again: %v", err)
	}
	defer s.Close()
	var body string
	if err := s.DB().QueryRow(`SELECT body FROM notes`).Scan(&body); err != nil {
		t.Fatalf("select: %v", err)
	}
	if body != "kept" {
		t.Errorf("body = %q, want kept", body)
	}
}
