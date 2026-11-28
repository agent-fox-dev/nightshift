package daemon_test

import (
	"bytes"
	"errors"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/daemon"
)

func TestRun_ConfigFailureStartsNothing(t *testing.T) {
	t.Parallel()
	var requests atomic.Int64
	hub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
	}))
	t.Cleanup(hub.Close)

	dir := t.TempDir()
	tokenFile := filepath.Join(dir, "token")
	storePath := filepath.Join(dir, "store", "store.db")
	configPath := filepath.Join(dir, "config.toml")
	content := "listen = \"127.0.0.1:0\"\n" +
		"token_file = \"" + tokenFile + "\"\n" +
		"[store]\npath = \"" + storePath + "\"\n" +
		"[hub]\nworker_token = \"parley_pat_w1_secret\"\napprover_token = \"parley_pat_a1_secret\"\n"
	if err := os.WriteFile(configPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	var stdout, stderr bytes.Buffer
	code := daemon.Run([]string{"--config", configPath}, daemon.Env{Home: dir, Stdout: &stdout, Stderr: &stderr})

	if code != 1 {
		t.Errorf("exit code = %d, want 1", code)
	}
	if got, want := stderr.String(), "config: hub.endpoint is not set\n"; got != want {
		t.Errorf("stderr = %q, want %q", got, want)
	}
	if n := requests.Load(); n != 0 {
		t.Errorf("the hub saw %d requests, want none", n)
	}
	for _, path := range []string{tokenFile, storePath} {
		if _, err := os.Stat(path); !errors.Is(err, fs.ErrNotExist) {
			t.Errorf("%s exists (stat error %v), want it not created", path, err)
		}
	}
}
