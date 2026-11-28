package hub

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
)

// Workspace is the part of Parley's workspace object nightshift reads.
type Workspace struct {
	Slug string `json:"slug"`
	// Kind is "repository" or "fork".
	Kind          string            `json:"kind"`
	DefaultBranch string            `json:"default_branch"`
	OwnerID       string            `json:"owner_id"`
	OrgSlug       string            `json:"org_slug"`
	HeadSHA       string            `json:"head_sha"`
	Settings      WorkspaceSettings `json:"settings"`
	// HubURL is the repository's git remote, nil when the hub has no
	// external URL.
	HubURL *string `json:"hub_url"`
	// Access is the caller's access: "owner", "member" or "admin".
	Access string `json:"access"`
}

// WorkspaceSettings is the part of a workspace's effective settings
// nightshift reads.
type WorkspaceSettings struct {
	SyncEnabled bool `json:"sync_enabled"`
}

// SyncBranch is one branch a sync reports.
type SyncBranch struct {
	Name string `json:"name"`
	// Result is one of created, up_to_date, fast_forward, ahead, diverged,
	// reset and name_conflict.
	Result    string `json:"result"`
	LocalSHA  string `json:"local_sha"`
	OriginSHA string `json:"origin_sha"`
}

// SyncResult is a sync's answer: the workspace object after the sync and
// every branch it reports.
type SyncResult struct {
	Workspace
	Branches []SyncBranch `json:"branches"`
}

func workspacePath(slug string) string {
	return "/api/v1/workspaces/" + url.PathEscape(slug)
}

// Workspace reads the workspace object of slug with the worker credential.
// An answer of the hub is a *Error in the returned error; a hub that gave
// no answer is not.
func (c *Client) Workspace(ctx context.Context, slug string) (Workspace, error) {
	var w Workspace
	if err := c.call(ctx, http.MethodGet, workspacePath(slug), &w); err != nil {
		return Workspace{}, fmt.Errorf("reading the workspace %s: %w", slug, err)
	}
	return w, nil
}

// Sync asks the hub to sync the workspace slug from its origin, with the
// worker credential, and returns what the sync reports. An answer of the
// hub is a *Error in the returned error; a hub that gave no answer is not.
func (c *Client) Sync(ctx context.Context, slug string) (SyncResult, error) {
	var res SyncResult
	if err := c.call(ctx, http.MethodPost, workspacePath(slug)+"/sync", &res); err != nil {
		return SyncResult{}, fmt.Errorf("syncing the workspace %s: %w", slug, err)
	}
	return res, nil
}

// call sends a request without a body for path with the worker credential
// and decodes the answer into v.
func (c *Client) call(ctx context.Context, method, path string, v any) error {
	req, err := c.NewRequest(ctx, method, path, nil)
	if err != nil {
		return err
	}
	resp, err := c.Do(ctx, Worker, req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if err := json.NewDecoder(resp.Body).Decode(v); err != nil {
		return fmt.Errorf("decoding the answer: %w", err)
	}
	return nil
}

// Secret is the text of cred's token as it is in use now, for a client
// that authenticates to the hub other than through Do, such as git over
// HTTP.
func (c *Client) Secret(cred Credential) string { return c.token(cred) }
