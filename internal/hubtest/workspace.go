package hubtest

import (
	"maps"
	"net/http"
	"slices"
	"testing"
)

// Workspace is a workspace the hub holds, with what its routes answer.
// AddWorkspace fills the empty members with their defaults.
type Workspace struct {
	Slug string
	// OrgSlug is the slug of the workspace's organisation; "acme" when
	// empty.
	OrgSlug string
	// Owner is the user id of the workspace's owner; Owner when empty.
	Owner string
	// Kind is "repository" or "fork"; "repository" when empty.
	Kind string
	// DefaultBranch is "main" when empty, the branch of the repository.
	DefaultBranch string
	// SyncDisabled turns the setting sync_enabled off; a sync then answers
	// 400 sync_disabled.
	SyncDisabled bool
	// Access is the access the hub answers, "member" or "admin", for a
	// caller who is not the owner, by user id. A caller who is neither the
	// owner nor named here cannot read the workspace and is answered 404.
	Access map[string]string
	// NoGitURL makes hub_url null, as a hub without an external URL
	// answers it.
	NoGitURL bool
	// Branches is what a sync reports. Nil reports the default branch
	// up_to_date at the repository's tip; a branch without LocalSHA or
	// OriginSHA reports the tip there.
	Branches []SyncBranch
	// SyncError, when set, is the error every sync answers.
	SyncError *ErrorAnswer
}

// SyncBranch is one branch of a sync's answer.
type SyncBranch struct {
	Name      string `json:"name"`
	Result    string `json:"result"`
	LocalSHA  string `json:"local_sha"`
	OriginSHA string `json:"origin_sha"`
}

// ErrorAnswer is an error the hub is scripted to answer, in Parley's
// envelope.
type ErrorAnswer struct {
	Status        int
	Name, Message string
}

// AddWorkspace gives the hub the workspace w with a repository of its own,
// a fresh copy of the fixture served at /git/<org>/<slug>.git, which its
// hub_url names unless w.NoGitURL. It returns the repository.
func (h *Hub) AddWorkspace(t testing.TB, w Workspace) *Repository {
	t.Helper()
	if w.OrgSlug == "" {
		w.OrgSlug = "acme"
	}
	if w.Owner == "" {
		w.Owner = Owner
	}
	if w.Kind == "" {
		w.Kind = "repository"
	}
	if w.DefaultBranch == "" {
		w.DefaultBranch = "main"
	}
	w = cloneWorkspace(w)
	repo := h.AddRepository(t, w.OrgSlug, w.Slug)
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.workspaces == nil {
		h.workspaces = map[string]*Workspace{}
	}
	h.workspaces[w.Slug] = &w
	return repo
}

// UpdateWorkspace changes the workspace slug the hub holds with fn, for a
// test that changes it after it was added.
func (h *Hub) UpdateWorkspace(slug string, fn func(*Workspace)) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if w, ok := h.workspaces[slug]; ok {
		fn(w)
		*w = cloneWorkspace(*w)
	}
}

func cloneWorkspace(w Workspace) Workspace {
	w.Access = maps.Clone(w.Access)
	w.Branches = slices.Clone(w.Branches)
	if w.SyncError != nil {
		e := *w.SyncError
		w.SyncError = &e
	}
	return w
}

// ownerOf is the owner of the workspace slug, Owner for one the hub does
// not hold. The caller holds h.mu.
func (h *Hub) ownerOf(slug string) string {
	if w, ok := h.workspaces[slug]; ok {
		return w.Owner
	}
	return Owner
}

// readableWorkspace authenticates the request and finds the workspace of
// its path with the caller's access, or answers as Parley does: 401, 403
// for a token without scope, 404 for a workspace the caller cannot read.
func (h *Hub) readableWorkspace(w http.ResponseWriter, r *http.Request, scopes ...string) (Workspace, string, bool) {
	caller, ok := h.authenticate(w, r)
	if !ok {
		return Workspace{}, "", false
	}
	if !slices.ContainsFunc(scopes, func(s string) bool { return slices.Contains(caller.Permissions, s) }) {
		h.fail(w, r, http.StatusForbidden, "insufficient_scope", "the token lacks "+scopes[0])
		return Workspace{}, "", false
	}
	h.mu.Lock()
	ws, known := h.workspaces[r.PathValue("slug")]
	var copied Workspace
	if known {
		copied = cloneWorkspace(*ws)
	}
	h.mu.Unlock()
	access := "owner"
	if known && caller.UserID != copied.Owner {
		access = copied.Access[caller.UserID]
	}
	if !known || access == "" {
		h.fail(w, r, http.StatusNotFound, "not_found", "no such workspace")
		return Workspace{}, "", false
	}
	return copied, access, true
}

// workspaceObject is Parley's workspace object of ws as the caller with
// access reads it.
func (h *Hub) workspaceObject(ws Workspace, access string) map[string]any {
	head := h.head(ws)
	var hubURL any
	if !ws.NoGitURL {
		hubURL = h.URL() + "/git/" + ws.OrgSlug + "/" + ws.Slug + ".git"
	}
	return map[string]any{
		"slug":            ws.Slug,
		"kind":            ws.Kind,
		"default_branch":  ws.DefaultBranch,
		"owner_id":        ws.Owner,
		"org_id":          "org_" + ws.OrgSlug,
		"org_slug":        ws.OrgSlug,
		"status":          "active",
		"head_sha":        head,
		"origin_head_sha": head,
		"settings":        map[string]any{"sync_enabled": !ws.SyncDisabled},
		"hub_url":         hubURL,
		"access":          access,
	}
}

// head is the tip of the workspace's repository, or empty.
func (h *Hub) head(ws Workspace) string {
	repo := h.Repository(ws.OrgSlug, ws.Slug)
	if repo == nil {
		return ""
	}
	sha, err := runGit(repo.Dir(), nil, "", "rev-parse", "HEAD")
	if err != nil {
		return ""
	}
	return sha
}

func (h *Hub) getWorkspace(w http.ResponseWriter, r *http.Request) {
	ws, access, ok := h.readableWorkspace(w, r, "workspaces:read", "workspaces:write")
	if !ok {
		return
	}
	h.answer(w, r, http.StatusOK, "", h.workspaceObject(ws, access))
}

func (h *Hub) syncWorkspace(w http.ResponseWriter, r *http.Request) {
	ws, access, ok := h.readableWorkspace(w, r, "workspaces:write")
	if !ok {
		return
	}
	switch {
	case ws.SyncError != nil:
		h.fail(w, r, ws.SyncError.Status, ws.SyncError.Name, ws.SyncError.Message)
		return
	case ws.SyncDisabled:
		h.fail(w, r, http.StatusBadRequest, "sync_disabled", "sync is disabled for this workspace")
		return
	}
	head := h.head(ws)
	branches := ws.Branches
	if branches == nil {
		branches = []SyncBranch{{Name: ws.DefaultBranch, Result: "up_to_date"}}
	}
	for i := range branches {
		if branches[i].LocalSHA == "" {
			branches[i].LocalSHA = head
		}
		if branches[i].OriginSHA == "" {
			branches[i].OriginSHA = head
		}
	}
	body := h.workspaceObject(ws, access)
	body["branches"] = branches
	h.answer(w, r, http.StatusOK, "", body)
}
