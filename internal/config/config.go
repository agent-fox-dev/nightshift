// Package config loads the daemon's configuration file.
package config

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/BurntSushi/toml"
)

// Config is the daemon's configuration. Later packages add their keys
// here; a key the struct does not hold is refused by Load.
type Config struct {
	Listen    string `toml:"listen"`
	TokenFile string `toml:"token_file"`
	// ClonesDir holds the workspaces' clones, one directory per slug.
	ClonesDir string      `toml:"clones_dir"`
	Store     StoreConfig `toml:"store"`
	Hub       HubConfig   `toml:"hub"`
}

// StoreConfig is the [store] table.
type StoreConfig struct {
	Path string `toml:"path"`
}

// HubConfig is the [hub] table.
type HubConfig struct {
	Endpoint      string `toml:"endpoint"`
	WorkerToken   string `toml:"worker_token"`
	ApproverToken string `toml:"approver_token"`
}

// Defaults of the keys a file may omit.
const (
	DefaultListen    = "127.0.0.1:7878"
	DefaultTokenFile = "~/.nightshift/token"
	DefaultStorePath = "~/.nightshift/store.db"
	DefaultClonesDir = "~/.nightshift/clones"
)

// DefaultPath is the configuration file read when no path is given.
func DefaultPath(home string) string {
	return filepath.Join(home, ".nightshift", "config.toml")
}

// Load reads the configuration file at path, or at DefaultPath(home) when
// path is empty. It applies the defaults and expands a leading "~/" of the
// path values against home. The error's text is the reason alone, without
// the name of the start check.
func Load(path, home string) (Config, error) {
	if path == "" {
		if home == "" {
			return Config{}, errors.New("no --config given and the home directory is unknown")
		}
		path = DefaultPath(home)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		// The *PathError names the file already.
		return Config{}, err
	}

	var cfg Config
	md, err := toml.NewDecoder(bytes.NewReader(data)).Decode(&cfg)
	if err != nil {
		return Config{}, fmt.Errorf("%s: %w", path, err)
	}
	// A misspelled key would otherwise be ignored silently.
	if undecoded := md.Undecoded(); len(undecoded) > 0 {
		return Config{}, fmt.Errorf("unknown key %q", undecoded[0].String())
	}

	required := []struct{ key, value string }{
		{"hub.endpoint", cfg.Hub.Endpoint},
		{"hub.worker_token", cfg.Hub.WorkerToken},
		{"hub.approver_token", cfg.Hub.ApproverToken},
	}
	for _, r := range required {
		if r.value == "" {
			return Config{}, fmt.Errorf("%s is not set", r.key)
		}
	}

	if cfg.Listen == "" {
		cfg.Listen = DefaultListen
	}
	if cfg.TokenFile == "" {
		cfg.TokenFile = DefaultTokenFile
	}
	if cfg.Store.Path == "" {
		cfg.Store.Path = DefaultStorePath
	}
	if cfg.ClonesDir == "" {
		cfg.ClonesDir = DefaultClonesDir
	}
	if cfg.TokenFile, err = expandHome(cfg.TokenFile, home); err != nil {
		return Config{}, fmt.Errorf("token_file: %w", err)
	}
	if cfg.Store.Path, err = expandHome(cfg.Store.Path, home); err != nil {
		return Config{}, fmt.Errorf("store.path: %w", err)
	}
	if cfg.ClonesDir, err = expandHome(cfg.ClonesDir, home); err != nil {
		return Config{}, fmt.Errorf("clones_dir: %w", err)
	}
	return cfg, nil
}

// expandHome replaces a leading "~/" of path with home.
func expandHome(path, home string) (string, error) {
	rest, ok := strings.CutPrefix(path, "~/")
	if !ok {
		return path, nil
	}
	if home == "" {
		return "", fmt.Errorf("cannot expand %q: the home directory is unknown", path)
	}
	return filepath.Join(home, rest), nil
}
