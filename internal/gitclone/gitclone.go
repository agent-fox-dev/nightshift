// Package gitclone brings a clone of a hub repository to a commit: it
// clones the repository when the directory holds none, fetches into the
// clone when it does, and checks the commit out detached with nothing an
// earlier use left. Every git command runs through a command.Runner, as
// the daemon's user or the identity the runner was given.
package gitclone

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/command"
)

// DefaultTimeout is how long one git command may run when Options names no
// timeout.
const DefaultTimeout = 10 * time.Minute

// tailLines is how much of a git command's output is kept: its last line
// is what an error reports.
const tailLines = 20

// Options says which repository to bring to which commit, where.
type Options struct {
	// URL is the repository's remote, the hub's hub_url; it carries no
	// credential.
	URL string
	// Token is the hub token git authenticates with, as the HTTP Basic
	// password.
	Token string
	// Dir is the clone's directory.
	Dir string
	// Commit is the full id of the commit to check out.
	Commit string
	// Runner runs every git command.
	Runner *command.Runner
	// Timeout bounds each git command; zero is DefaultTimeout.
	Timeout time.Duration
}

// Sync brings the clone in opts.Dir to opts.Commit. A missing or empty
// directory is cloned into, a clone whose origin is opts.URL is fetched
// into, and a directory holding anything else is removed and cloned anew.
// The commit is then checked out detached, with every change to tracked
// files discarded and every untracked and ignored file removed.
//
// The token reaches git only through the environment of its processes,
// never an argument, the remote's URL or the clone's configuration, and no
// error text holds it. A failed first clone leaves no directory behind.
func Sync(ctx context.Context, opts Options) error {
	if opts.URL == "" || opts.Dir == "" || opts.Commit == "" || opts.Runner == nil {
		return errors.New("gitclone: the URL, directory, commit and runner are required")
	}
	s := &syncer{opts: opts, header: "Authorization: Basic " + base64.StdEncoding.EncodeToString([]byte("x:"+opts.Token))}
	if s.opts.Timeout <= 0 {
		s.opts.Timeout = DefaultTimeout
	}
	fresh, err := s.prepare(ctx)
	if err != nil {
		return s.clean(err)
	}
	if err := s.bring(ctx, fresh); err != nil {
		if fresh {
			os.RemoveAll(opts.Dir)
		}
		return s.clean(err)
	}
	return nil
}

type syncer struct {
	opts   Options
	header string // the HTTP header that carries the token
}

// prepare leaves opts.Dir an existing directory that is either empty or a
// clone of opts.URL, and reports whether it is empty.
func (s *syncer) prepare(ctx context.Context) (fresh bool, err error) {
	entries, err := os.ReadDir(s.opts.Dir)
	switch {
	case errors.Is(err, os.ErrNotExist):
		return true, os.MkdirAll(s.opts.Dir, 0o755)
	case err != nil:
		return false, fmt.Errorf("gitclone: %w", err)
	case len(entries) == 0:
		return true, nil
	}
	if s.isCloneOfURL(ctx) {
		return false, s.reset()
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if err := os.RemoveAll(s.opts.Dir); err != nil {
		return false, fmt.Errorf("gitclone: removing %s: %w", s.opts.Dir, err)
	}
	return true, os.MkdirAll(s.opts.Dir, 0o755)
}

// isCloneOfURL reports whether opts.Dir is itself a clone, not a directory
// inside another one, whose origin is opts.URL.
func (s *syncer) isCloneOfURL(ctx context.Context) bool {
	if info, err := os.Stat(filepath.Join(s.opts.Dir, ".git")); err != nil || !info.IsDir() {
		return false
	}
	res, err := s.opts.Runner.Run(ctx, s.spec("git remote get-url origin"))
	return err == nil && res.ExitCode == 0 && len(res.Tail) > 0 && res.Tail[len(res.Tail)-1] == s.opts.URL
}

// reset gives a kept clone back the configuration a fresh one has and no
// hooks. A command that ran in the clone (a health command) may have
// written into its .git: a hook, a filter, a proxy or a URL rewrite there
// would run with, or send away, the credential the next git command
// carries.
func (s *syncer) reset() error {
	gitDir := filepath.Join(s.opts.Dir, ".git")
	if err := os.RemoveAll(filepath.Join(gitDir, "hooks")); err != nil {
		return fmt.Errorf("gitclone: removing the clone's hooks: %w", err)
	}
	config := "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n" +
		"[remote \"origin\"]\n\turl = " + s.opts.URL + "\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
	if err := os.WriteFile(filepath.Join(gitDir, "config"), []byte(config), 0o644); err != nil {
		return fmt.Errorf("gitclone: resetting the clone's configuration: %w", err)
	}
	return nil
}

// bring fetches into the clone, making one first in an empty directory,
// and checks the commit out.
func (s *syncer) bring(ctx context.Context, fresh bool) error {
	steps := []string{}
	if fresh {
		// No template: nothing of the machine's git templates, hooks
		// among them, comes into the clone.
		steps = append(steps, "git init -q --template=", "git remote add origin "+quote(s.opts.URL))
	}
	steps = append(steps, "git fetch -q --prune --tags origin")
	for _, line := range steps {
		if err := s.git(ctx, line); err != nil {
			return err
		}
	}
	commit := quote(s.opts.Commit + "^{commit}")
	res, err := s.opts.Runner.Run(ctx, s.spec("git cat-file -e "+commit))
	if err != nil {
		return fmt.Errorf("gitclone: git cat-file: %w", err)
	}
	if res.TimedOut || res.ExitCode != 0 {
		return fmt.Errorf("gitclone: commit %s not found", s.opts.Commit)
	}
	for _, line := range []string{
		"git checkout -q -f --detach " + commit,
		"git reset -q --hard",
		"git clean -q -ffdx",
	} {
		if err := s.git(ctx, line); err != nil {
			return err
		}
	}
	return nil
}

// git runs one git command line in the clone and turns its failure into an
// error holding git's last line of output.
func (s *syncer) git(ctx context.Context, line string) error {
	name := strings.Join(strings.Fields(line)[:2], " ")
	res, err := s.opts.Runner.Run(ctx, s.spec(line))
	if err != nil {
		return fmt.Errorf("gitclone: %s: %w", name, err)
	}
	last := ""
	if len(res.Tail) > 0 {
		last = ": " + res.Tail[len(res.Tail)-1]
	}
	if res.TimedOut {
		return fmt.Errorf("gitclone: %s timed out after %v%s", name, s.opts.Timeout, last)
	}
	if res.ExitCode != 0 {
		return fmt.Errorf("gitclone: %s exited %d%s", name, res.ExitCode, last)
	}
	return nil
}

// spec is a git command line in the clone, with the credential in the
// environment as an extra HTTP header, no credential helper that could
// store or replace it, no hook and no file-system monitor that could run
// with it, and no prompt that could wait for a person. These variables
// take precedence over every configuration file.
func (s *syncer) spec(line string) command.Spec {
	return command.Spec{
		Dir:       s.opts.Dir,
		Line:      line,
		Timeout:   s.opts.Timeout,
		TailLines: tailLines,
		Env: []string{
			"GIT_CONFIG_COUNT=4",
			"GIT_CONFIG_KEY_0=http.extraHeader",
			"GIT_CONFIG_VALUE_0=" + s.header,
			"GIT_CONFIG_KEY_1=credential.helper",
			"GIT_CONFIG_VALUE_1=",
			"GIT_CONFIG_KEY_2=core.hooksPath",
			"GIT_CONFIG_VALUE_2=" + os.DevNull,
			"GIT_CONFIG_KEY_3=core.fsmonitor",
			"GIT_CONFIG_VALUE_3=false",
			"GIT_TERMINAL_PROMPT=0",
			"GIT_ASKPASS=",
		},
	}
}

// clean takes the token, and the header that carries it, out of an error's
// text.
func (s *syncer) clean(err error) error {
	text := err.Error()
	cleaned := text
	for _, secret := range []string{s.header, strings.TrimPrefix(s.header, "Authorization: Basic "), s.opts.Token} {
		if secret != "" {
			cleaned = strings.ReplaceAll(cleaned, secret, "<redacted>")
		}
	}
	if cleaned == text {
		return err
	}
	var cause error
	for _, ctxErr := range []error{context.Canceled, context.DeadlineExceeded} {
		if errors.Is(err, ctxErr) {
			cause = ctxErr
		}
	}
	return redacted{text: cleaned, cause: cause}
}

// redacted is an error whose text had the credential taken out. It unwraps
// to a context's error only, never to the error that held the credential.
type redacted struct {
	text  string
	cause error
}

func (r redacted) Error() string { return r.text }
func (r redacted) Unwrap() error { return r.cause }

// quote quotes s for sh, so that no character of it is the shell's.
func quote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
