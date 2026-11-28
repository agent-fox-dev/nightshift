package config_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/config"
)

const hubKeys = `
[hub]
endpoint = "http://hub.example:8080"
worker_token = "parley_pat_w1_secret"
approver_token = "parley_pat_a1_secret"
`

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestLoad_ReadsEveryKeyFromTheFile(t *testing.T) {
	t.Parallel()
	const content = `
listen = "127.0.0.1:9999"
token_file = "/var/lib/nightshift/token"

[store]
path = "~/x.db"
` + hubKeys

	tests := []struct {
		name string
		// file is where the configuration is written, relative to the home.
		file string
		// path is what Load is given: empty when --config is absent.
		path func(home string) string
	}{
		{"without a path the default file is read", ".nightshift/config.toml", func(string) string { return "" }},
		{"a given path is read", "elsewhere/nightshift.toml", func(home string) string { return filepath.Join(home, "elsewhere/nightshift.toml") }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			home := t.TempDir()
			writeFile(t, filepath.Join(home, tt.file), content)

			cfg, err := config.Load(tt.path(home), home)
			if err != nil {
				t.Fatalf("Load: %v", err)
			}
			want := config.Config{
				Listen:    "127.0.0.1:9999",
				TokenFile: "/var/lib/nightshift/token",
				Store:     config.StoreConfig{Path: filepath.Join(home, "x.db")},
				Hub: config.HubConfig{
					Endpoint:      "http://hub.example:8080",
					WorkerToken:   "parley_pat_w1_secret",
					ApproverToken: "parley_pat_a1_secret",
				},
			}
			if cfg != want {
				t.Errorf("Load = %+v, want %+v", cfg, want)
			}
		})
	}
}

func TestLoad_DefaultsOmittedKeys(t *testing.T) {
	t.Parallel()
	home := t.TempDir()
	path := filepath.Join(home, "config.toml")
	writeFile(t, path, hubKeys)

	cfg, err := config.Load(path, home)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Listen != "127.0.0.1:7878" {
		t.Errorf("Listen = %q, want 127.0.0.1:7878", cfg.Listen)
	}
	if want := filepath.Join(home, ".nightshift", "token"); cfg.TokenFile != want {
		t.Errorf("TokenFile = %q, want %q", cfg.TokenFile, want)
	}
	if want := filepath.Join(home, ".nightshift", "store.db"); cfg.Store.Path != want {
		t.Errorf("Store.Path = %q, want %q", cfg.Store.Path, want)
	}
}

func TestLoad_RejectsBadConfiguration(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		// content is nil when no file is written.
		content *string
		// want is the error text, with %s standing for the file's path.
		want string
		// prefixOnly accepts any text after want, for errors whose detail
		// is the TOML decoder's own.
		prefixOnly bool
	}{
		{name: "missing file", want: "open %s: no such file or directory"},
		{name: "malformed", content: ptr("listen = \n" + hubKeys), want: "%s: ", prefixOnly: true},
		{name: "unknown key", content: ptr(strings.Replace(hubKeys, "endpoint", "endpont", 1)), want: `unknown key "hub.endpont"`},
		{name: "no endpoint", content: ptr(dropLine(hubKeys, "endpoint")), want: "hub.endpoint is not set"},
		{name: "no worker token", content: ptr(dropLine(hubKeys, "worker_token")), want: "hub.worker_token is not set"},
		{name: "no approver token", content: ptr(dropLine(hubKeys, "approver_token")), want: "hub.approver_token is not set"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			home := t.TempDir()
			path := filepath.Join(home, "config.toml")
			if tt.content != nil {
				writeFile(t, path, *tt.content)
			}

			_, err := config.Load(path, home)
			if err == nil {
				t.Fatal("Load succeeded, want an error")
			}
			want := strings.ReplaceAll(tt.want, "%s", path)
			got := err.Error()
			if tt.prefixOnly {
				if !strings.HasPrefix(got, want) || len(got) == len(want) {
					t.Errorf("Load error = %q, want %q followed by the decoder's reason", got, want)
				}
				return
			}
			if got != want {
				t.Errorf("Load error = %q, want %q", got, want)
			}
		})
	}
}

func ptr(s string) *string { return &s }

// dropLine removes the line that sets key.
func dropLine(content, key string) string {
	var kept []string
	for line := range strings.SplitSeq(content, "\n") {
		if !strings.HasPrefix(line, key+" ") {
			kept = append(kept, line)
		}
	}
	return strings.Join(kept, "\n")
}
