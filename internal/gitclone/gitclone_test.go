package gitclone_test

import (
	"bytes"
	"io/fs"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/agent-fox-dev/nightshift/internal/command"
	"github.com/agent-fox-dev/nightshift/internal/gitclone"
	"github.com/agent-fox-dev/nightshift/internal/hubtest"
)

func TestSync_ClonesAtTheCommitWithoutKeepingTheToken(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	repo := h.AddRepository(t, "acme", "demo")
	worker, _ := h.GoodTokens()
	first := repo.Commits(t)[0]
	runner, seen := recordingRunner()
	dir := filepath.Join(t.TempDir(), "demo")

	err := gitclone.Sync(t.Context(), gitclone.Options{URL: repo.URL(), Token: worker, Dir: dir, Commit: first, Runner: runner})
	if err != nil {
		t.Fatalf("Sync: %v", err)
	}

	if head := git(t, dir, "rev-parse", "HEAD"); head != first {
		t.Errorf("HEAD is %s, want %s", head, first)
	}
	if git(t, dir, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD" {
		t.Errorf("HEAD is on a branch, want it detached")
	}
	want := strings.Fields(git(t, repo.Dir(), "ls-tree", "-r", "--name-only", first))
	if got := workTreeFiles(t, dir); !slices.Equal(got, want) {
		t.Errorf("the work tree holds %q, want the first commit's %q", got, want)
	}
	if origin := git(t, dir, "remote", "get-url", "origin"); origin != repo.URL() {
		t.Errorf("origin is %q, want %q without a credential", origin, repo.URL())
	}
	filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		data, _ := os.ReadFile(path)
		if bytes.Contains(data, []byte(worker)) || bytes.Contains(data, []byte("Authorization")) {
			t.Errorf("%s holds the credential", path)
		}
		return nil
	})
	for _, args := range seen() {
		if strings.Contains(args, worker) {
			t.Errorf("a git process was given the token in its arguments: %s", args)
		}
	}
	fetches := h.Fetches()
	if len(fetches) == 0 {
		t.Fatalf("the hub saw no fetch")
	}
	for _, req := range fetches[0].Requests {
		if req.Basic != worker || req.Status != 200 {
			t.Errorf("git request %s %s: password %q, status %d; want the worker token and 200", req.Method, req.Path, req.Basic, req.Status)
		}
	}
}

func TestSync_BringsAnExistingDirectoryToTheCommit(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		// prepare fills the directory before the Sync under test and says
		// whether the Sync must keep the clone it made.
		prepare func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts gitclone.Options) (keeps bool)
	}{
		{name: "a clone of the URL with changes, untracked and ignored files", prepare: func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts gitclone.Options) bool {
			opts.Commit = repo.Commits(t)[0]
			if err := gitclone.Sync(t.Context(), opts); err != nil {
				t.Fatalf("first Sync: %v", err)
			}
			write(t, opts.Dir, "notes.txt", "untracked\n")
			write(t, opts.Dir, "debug.log", "ignored\n")
			write(t, opts.Dir, "README.md", "changed\n")
			write(t, opts.Dir, ".git/kept", "a clone that is fetched into keeps this\n")
			return true
		}},
		{name: "a clone of another URL", prepare: func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts gitclone.Options) bool {
			other := h.AddRepository(t, "acme", "other")
			opts.URL, opts.Commit = other.URL(), other.Head(t)
			if err := gitclone.Sync(t.Context(), opts); err != nil {
				t.Fatalf("Sync of the other repository: %v", err)
			}
			return false
		}},
		{name: "a directory that is no clone", prepare: func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts gitclone.Options) bool {
			write(t, opts.Dir, "stray.txt", "not a clone\n")
			return false
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			repo := h.AddRepository(t, "acme", "demo")
			worker, _ := h.GoodTokens()
			runner, _ := recordingRunner()
			opts := gitclone.Options{URL: repo.URL(), Token: worker, Dir: filepath.Join(t.TempDir(), "demo"), Runner: runner}
			keeps := tt.prepare(t, h, repo, opts)

			opts.Commit = repo.AddCommit(t, map[string]string{"CHANGES.md": "third\n"}, "Note the changes")
			if err := gitclone.Sync(t.Context(), opts); err != nil {
				t.Fatalf("Sync: %v", err)
			}

			if head := git(t, opts.Dir, "rev-parse", "HEAD"); head != opts.Commit {
				t.Errorf("HEAD is %s, want %s", head, opts.Commit)
			}
			if status := git(t, opts.Dir, "status", "--porcelain", "--ignored"); status != "" {
				t.Errorf("git status --porcelain --ignored printed %q, want nothing", status)
			}
			if origin := git(t, opts.Dir, "remote", "get-url", "origin"); origin != repo.URL() {
				t.Errorf("origin is %q, want %q", origin, repo.URL())
			}
			if _, err := os.Stat(filepath.Join(opts.Dir, ".git", "kept")); (err == nil) != keeps {
				t.Errorf("the clone was kept: %v, want %v", err == nil, keeps)
			}
		})
	}
}

func TestSync_FailsWithoutLeavingAHalfMadeClone(t *testing.T) {
	t.Parallel()
	const zeros = "0000000000000000000000000000000000000000"
	tests := []struct {
		name string
		// change breaks the options in one way.
		change func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts *gitclone.Options)
		want   string
	}{
		{name: "a token the hub does not know", want: "fatal:", change: func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts *gitclone.Options) {
			opts.Token = "parley_pat_unknown_secret"
		}},
		{name: "a token without git:read", want: "fatal:", change: func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts *gitclone.Options) {
			opts.Token = h.AddToken(hubtest.Token{Permissions: []string{"workspaces:read"}})
		}},
		{name: "a URL nothing listens on", want: "fatal:", change: func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts *gitclone.Options) {
			ln, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			ln.Close()
			opts.URL = "http://" + ln.Addr().String() + repo.Path()
		}},
		{name: "a commit the repository does not hold", want: "commit " + zeros + " not found", change: func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts *gitclone.Options) {
			opts.Commit = zeros
		}},
		{name: "a git command that does not end in time", want: "timed out", change: func(t *testing.T, h *hubtest.Hub, repo *hubtest.Repository, opts *gitclone.Options) {
			repo.SetAnswer(hubtest.Hang)
			opts.Timeout = 50 * time.Millisecond
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			h := hubtest.New(t)
			repo := h.AddRepository(t, "acme", "demo")
			worker, _ := h.GoodTokens()
			runner, _ := recordingRunner()
			good := gitclone.Options{URL: repo.URL(), Token: worker, Dir: filepath.Join(t.TempDir(), "demo"), Commit: repo.Head(t), Runner: runner}
			bad := good
			tt.change(t, h, repo, &bad)

			err := gitclone.Sync(t.Context(), bad)
			if err == nil {
				t.Fatalf("Sync succeeded, want an error")
			}
			if !strings.Contains(err.Error(), tt.want) {
				t.Errorf("error %q, want it to hold %q", err, tt.want)
			}
			if strings.Contains(err.Error(), bad.Token) || strings.Contains(err.Error(), worker) {
				t.Errorf("error %q holds the token", err)
			}
			if _, err := os.Stat(good.Dir); err == nil {
				t.Errorf("the failed first clone left %s", good.Dir)
			}

			repo.SetAnswer(0)
			if err := gitclone.Sync(t.Context(), good); err != nil {
				t.Fatalf("Sync with good options after the failure: %v", err)
			}
			if head := git(t, good.Dir, "rev-parse", "HEAD"); head != good.Commit {
				t.Errorf("HEAD is %s, want %s", head, good.Commit)
			}
		})
	}
}

// recordingRunner is a runner whose identity records the arguments of
// every process it starts, which shows what git was given.
func recordingRunner() (*command.Runner, func() []string) {
	var mu sync.Mutex
	var seen []string
	runner := &command.Runner{}
	runner.SetIdentity(func(cmd *exec.Cmd) error {
		mu.Lock()
		defer mu.Unlock()
		seen = append(seen, strings.Join(cmd.Args, " "))
		return nil
	})
	return runner, func() []string {
		mu.Lock()
		defer mu.Unlock()
		return slices.Clone(seen)
	}
}

// git runs git in dir and returns its trimmed output.
func git(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %s in %s: %v: %s", strings.Join(args, " "), dir, err, out)
	}
	return strings.TrimSpace(string(out))
}

// workTreeFiles is every file under dir outside .git, sorted.
func workTreeFiles(t *testing.T, dir string) []string {
	t.Helper()
	var files []string
	err := filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() && d.Name() == ".git" {
			return filepath.SkipDir
		}
		if !d.IsDir() {
			rel, _ := filepath.Rel(dir, path)
			files = append(files, rel)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	slices.Sort(files)
	return files
}

func write(t *testing.T, dir, name, content string) {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
