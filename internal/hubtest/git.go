package hubtest

import (
	"bytes"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net/http"
	"net/http/cgi"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
)

// Repository is a real git repository the hub serves over smart HTTP at
// /git/<org>/<slug>.git, as Parley serves a workspace's repository. It
// starts as a copy of the fixture: README.md and .gitignore in a first
// commit, main.go added in a second, on the branch main.
type Repository struct {
	hub       *Hub
	org, slug string
	root      string // GIT_PROJECT_ROOT of the repository's git http-backend
	backend   *cgi.Handler
	// answer is guarded by hub.mu.
	answer int
}

// AddRepository gives the hub a repository at /git/<org>/<slug>.git, a
// fresh copy of the fixture in a directory of the test's own.
func (h *Hub) AddRepository(t testing.TB, org, slug string) *Repository {
	t.Helper()
	fixture.once.Do(func() { fixture.files, fixture.err = buildFixture() })
	if fixture.err != nil {
		t.Fatalf("hubtest: building the repository fixture: %v", fixture.err)
	}
	gitBin, err := exec.LookPath("git")
	if err != nil {
		t.Fatalf("hubtest: %v", err)
	}
	root := t.TempDir()
	dir := filepath.Join(root, org, slug+".git")
	for _, f := range fixture.files {
		path := filepath.Join(dir, f.path)
		if f.dir {
			err = os.MkdirAll(path, 0o755)
		} else {
			err = os.WriteFile(path, f.data, f.mode)
		}
		if err != nil {
			t.Fatalf("hubtest: copying the repository fixture: %v", err)
		}
	}
	r := &Repository{
		hub: h, org: org, slug: slug, root: root,
		backend: &cgi.Handler{
			Path: gitBin,
			Args: []string{"http-backend"},
			Root: "/git",
			Env: append([]string{"GIT_PROJECT_ROOT=" + root, "GIT_HTTP_EXPORT_ALL=1"},
				isolatedGitEnv...),
			Logger: log.New(io.Discard, "", 0),
			Stderr: io.Discard,
		},
	}
	h.mu.Lock()
	if h.repos == nil {
		h.repos = map[string]*Repository{}
	}
	h.repos[r.Path()] = r
	h.mu.Unlock()
	return r
}

// Repository is the repository at /git/<org>/<slug>.git, or nil.
func (h *Hub) Repository(org, slug string) *Repository {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.repos["/git/"+org+"/"+slug+".git"]
}

// Path is the repository's path on the hub, /git/<org>/<slug>.git.
func (r *Repository) Path() string { return "/git/" + r.org + "/" + r.slug + ".git" }

// URL is the repository's remote, as Parley's hub_url names it.
func (r *Repository) URL() string { return r.hub.URL() + r.Path() }

// Dir is the bare repository on disk.
func (r *Repository) Dir() string { return filepath.Join(r.root, r.org, r.slug+".git") }

// SetAnswer scripts the repository's git endpoint: 0 serves git, Hang
// holds every request until the client gives up or the hub stops, and any
// other status answers every request with it.
func (r *Repository) SetAnswer(status int) {
	r.hub.mu.Lock()
	defer r.hub.mu.Unlock()
	r.answer = status
}

// Commits is the commits of the branch main, oldest first.
func (r *Repository) Commits(t testing.TB) []string {
	t.Helper()
	out, err := runGit(r.Dir(), nil, "", "rev-list", "--reverse", "HEAD")
	if err != nil {
		t.Fatalf("hubtest: %v", err)
	}
	return strings.Fields(out)
}

// Head is the tip of the branch main.
func (r *Repository) Head(t testing.TB) string {
	t.Helper()
	commits := r.Commits(t)
	return commits[len(commits)-1]
}

// AddCommit commits files, path to content, on top of main and returns the
// new commit; the files of the commit before stay.
func (r *Repository) AddCommit(t testing.TB, files map[string]string, message string) string {
	t.Helper()
	sha, err := commit(r.Dir(), files, message)
	if err != nil {
		t.Fatalf("hubtest: adding a commit: %v", err)
	}
	return sha
}

// Fetch is one git fetch or clone as the hub saw it: a run of consecutive
// requests to one repository's git endpoint, since a fetch over smart
// HTTP is several requests.
type Fetch struct {
	// Repository is the repository's path, /git/<org>/<slug>.git.
	Repository string
	Requests   []Request
}

// Fetches is every fetch the hub saw, in order. Requests to another route
// or another repository end a run.
func (h *Hub) Fetches() []Fetch {
	var out []Fetch
	last := ""
	for _, req := range h.Requests() {
		repo := repositoryOf(req.Path)
		if repo == "" {
			last = ""
			continue
		}
		if repo != last {
			out = append(out, Fetch{Repository: repo})
			last = repo
		}
		out[len(out)-1].Requests = append(out[len(out)-1].Requests, req)
	}
	return out
}

// repositoryOf is the /git/<org>/<slug>.git prefix of a path, or empty.
func repositoryOf(path string) string {
	rest, ok := strings.CutPrefix(path, "/git/")
	if !ok {
		return ""
	}
	parts := strings.SplitN(rest, "/", 3)
	if len(parts) < 2 || !strings.HasSuffix(parts[1], ".git") {
		return ""
	}
	return "/git/" + parts[0] + "/" + parts[1]
}

// gitEndpoint serves a repository as Parley does: the credential is the
// Basic password, and it must be a known, unrevoked token holding git:read
// or git:write.
func (h *Hub) gitEndpoint(w http.ResponseWriter, r *http.Request) {
	h.mu.Lock()
	repo := h.repos[repositoryOf(r.URL.Path)]
	var answer int
	if repo != nil {
		answer = repo.answer
	}
	quit := h.quit
	h.mu.Unlock()

	if answer == Hang {
		select {
		case <-r.Context().Done():
		case <-quit:
		}
		return
	}
	if answer != 0 {
		h.fail(w, r, answer, "scripted", "a scripted answer of the git endpoint")
		return
	}

	_, pat, _ := r.BasicAuth()
	h.mu.Lock()
	tok, known := h.tokens[pat]
	var copied Token
	if known {
		copied = *tok
	}
	h.mu.Unlock()
	if !known || copied.Revoked ||
		(!slices.Contains(copied.Permissions, "git:read") && !slices.Contains(copied.Permissions, "git:write")) {
		w.Header().Set("WWW-Authenticate", `Basic realm="Parley"`)
		h.fail(w, r, http.StatusUnauthorized, "unauthenticated", "missing, unknown or revoked credential, or no git:read")
		return
	}
	if repo == nil {
		h.fail(w, r, http.StatusNotFound, "not_found", "no such repository")
		return
	}
	repo.backend.ServeHTTP(&noting{ResponseWriter: w, hub: h, r: r}, r)
}

// noting notes the status git http-backend answers in the request log
// before it reaches the client.
type noting struct {
	http.ResponseWriter
	hub   *Hub
	r     *http.Request
	wrote bool
}

func (n *noting) WriteHeader(status int) {
	if !n.wrote {
		n.wrote = true
		n.hub.note(n.r, status, "")
	}
	n.ResponseWriter.WriteHeader(status)
}

func (n *noting) Write(b []byte) (int, error) {
	if !n.wrote {
		n.WriteHeader(http.StatusOK)
	}
	return n.ResponseWriter.Write(b)
}

func (n *noting) Flush() {
	if f, ok := n.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// The fixture is built once per test process and kept in memory, so that
// nothing of it outlives the process; each repository is a copy.
var fixture struct {
	once  sync.Once
	files []fixtureFile
	err   error
}

type fixtureFile struct {
	path string
	dir  bool
	mode fs.FileMode
	data []byte
}

// isolatedGitEnv keeps the user's and the system's git configuration, and
// the time, out of the fixture, so that its commits are the same in every
// run.
var isolatedGitEnv = []string{
	"GIT_CONFIG_NOSYSTEM=1",
	"GIT_CONFIG_GLOBAL=" + os.DevNull,
	"GIT_AUTHOR_NAME=hubtest", "GIT_AUTHOR_EMAIL=hubtest@example.com",
	"GIT_COMMITTER_NAME=hubtest", "GIT_COMMITTER_EMAIL=hubtest@example.com",
	"GIT_AUTHOR_DATE=2026-11-28T00:00:00Z", "GIT_COMMITTER_DATE=2026-11-28T00:00:00Z",
}

func buildFixture() ([]fixtureFile, error) {
	scratch, err := os.MkdirTemp("", "hubtest-repo-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(scratch)
	dir := filepath.Join(scratch, "repo.git")
	if _, err := runGit(scratch, nil, "", "init", "-q", "--bare", "--template=", "--initial-branch=main", dir); err != nil {
		return nil, err
	}
	if _, err := commit(dir, map[string]string{
		"README.md":  "# demo\n\nThe fake hub's repository.\n",
		".gitignore": "*.log\n",
	}, "Start the demo"); err != nil {
		return nil, err
	}
	if _, err := commit(dir, map[string]string{
		"main.go": "package main\n\nfunc main() {}\n",
	}, "Add a program"); err != nil {
		return nil, err
	}
	var files []fixtureFile
	err = filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(dir, path)
		if d.IsDir() {
			files = append(files, fixtureFile{path: rel, dir: true})
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		files = append(files, fixtureFile{path: rel, mode: info.Mode().Perm(), data: data})
		return nil
	})
	return files, err
}

// commit writes files on top of the bare repository's HEAD with git's
// plumbing, which needs no work tree.
func commit(gitDir string, files map[string]string, message string) (string, error) {
	scratch, err := os.MkdirTemp("", "hubtest-index-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(scratch)
	env := []string{"GIT_DIR=" + gitDir, "GIT_INDEX_FILE=" + filepath.Join(scratch, "index")}
	parent, err := runGit(scratch, env, "", "rev-parse", "--verify", "-q", "HEAD")
	if err == nil {
		if _, err := runGit(scratch, env, "", "read-tree", parent); err != nil {
			return "", err
		}
	} else {
		parent = ""
	}
	paths := make([]string, 0, len(files))
	for path := range files {
		paths = append(paths, path)
	}
	slices.Sort(paths)
	for _, path := range paths {
		blob, err := runGit(scratch, env, files[path], "hash-object", "-w", "--stdin")
		if err != nil {
			return "", err
		}
		if _, err := runGit(scratch, env, "", "update-index", "--add", "--cacheinfo", "100644,"+blob+","+path); err != nil {
			return "", err
		}
	}
	tree, err := runGit(scratch, env, "", "write-tree")
	if err != nil {
		return "", err
	}
	args := []string{"commit-tree", tree, "-m", message}
	if parent != "" {
		args = append(args, "-p", parent)
	}
	sha, err := runGit(scratch, env, "", args...)
	if err != nil {
		return "", err
	}
	if _, err := runGit(scratch, env, "", "update-ref", "HEAD", sha); err != nil {
		return "", err
	}
	return sha, nil
}

// runGit runs git in dir and returns its trimmed standard output.
func runGit(dir string, env []string, stdin string, args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(append(os.Environ(), isolatedGitEnv...), env...)
	cmd.Stdin = strings.NewReader(stdin)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("git %s: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(stderr.String()))
	}
	return strings.TrimSpace(string(out)), nil
}
