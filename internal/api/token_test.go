package api_test

import (
	"os"
	"path/filepath"
	"regexp"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/api"
)

func TestEnsureClientToken_WritesANewToken(t *testing.T) {
	t.Parallel()
	dir := filepath.Join(t.TempDir(), "nightshift")
	path := filepath.Join(dir, "token")

	tok, err := api.EnsureClientToken(path)
	if err != nil {
		t.Fatalf("EnsureClientToken: %v", err)
	}

	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(tok) {
		t.Errorf("token = %q, want 64 lowercase hexadecimal characters", tok)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if mode := info.Mode().Perm(); mode != 0o600 {
		t.Errorf("token file mode = %o, want 600", mode)
	}
	dirInfo, err := os.Stat(dir)
	if err != nil {
		t.Fatal(err)
	}
	if mode := dirInfo.Mode().Perm(); mode != 0o700 {
		t.Errorf("parent directory mode = %o, want 700", mode)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(content) != tok+"\n" {
		t.Errorf("token file holds %q, want %q", content, tok+"\n")
	}
	other, err := api.EnsureClientToken(filepath.Join(t.TempDir(), "token"))
	if err != nil {
		t.Fatalf("EnsureClientToken of another file: %v", err)
	}
	if other == tok {
		t.Errorf("two new token files hold the same token %q", tok)
	}
}

func TestEnsureClientToken_KeepsAnExistingFile(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "token")
	first, err := api.EnsureClientToken(path)
	if err != nil {
		t.Fatalf("first EnsureClientToken: %v", err)
	}
	// An old modification time makes a rewrite visible whatever the file
	// system's time resolution.
	old := time.Now().Add(-time.Hour).Truncate(time.Second)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}

	second, err := api.EnsureClientToken(path)
	if err != nil {
		t.Fatalf("second EnsureClientToken: %v", err)
	}

	if second != first {
		t.Errorf("second token = %q, want the file's %q", second, first)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if !info.ModTime().Equal(old) {
		t.Errorf("modification time = %v, want it unchanged at %v", info.ModTime(), old)
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != string(before) {
		t.Errorf("token file holds %q, want it unchanged %q", after, before)
	}
}
