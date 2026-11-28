package api

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// EnsureClientToken returns the client token held in the file at path. An
// existing file is only read, never rewritten or re-permissioned, so that a
// client holding its token keeps working. Without one, it writes a new
// token of 32 random bytes as 64 lowercase hexadecimal characters and a
// newline, with mode 0600, creating the parent directory with mode 0700:
// only the person who can read the daemon's files may call the API.
func EnsureClientToken(path string) (string, error) {
	content, err := os.ReadFile(path)
	if err == nil {
		token := strings.TrimSpace(string(content))
		if token == "" {
			// An empty token would admit a request bearing an empty one.
			return "", fmt.Errorf("%s holds no token", path)
		}
		return token, nil
	}
	if !errors.Is(err, fs.ErrNotExist) {
		return "", err
	}

	var secret [32]byte
	if _, err := rand.Read(secret[:]); err != nil {
		return "", err
	}
	token := hex.EncodeToString(secret[:])
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return "", err
	}
	// O_EXCL refuses to overwrite a file another process wrote meanwhile.
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return "", err
	}
	if _, err := f.WriteString(token + "\n"); err != nil {
		f.Close()
		os.Remove(path)
		return "", err
	}
	if err := f.Close(); err != nil {
		os.Remove(path)
		return "", err
	}
	return token, nil
}
