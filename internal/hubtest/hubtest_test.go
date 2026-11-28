package hubtest_test

import (
	"errors"
	"net/http"
	"syscall"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/hubtest"
)

func TestHub_StartAfterStopServesTheSameAddress(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	url := h.URL() + "/readyz"

	h.Stop()
	if _, err := http.Get(url); !errors.Is(err, syscall.ECONNREFUSED) {
		t.Fatalf("GET after Stop: %v, want connection refused", err)
	}

	h.Start(t)
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("GET after Start: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("readyz answered %d, want 200", resp.StatusCode)
	}
	if n := len(h.Requests()); n != 1 {
		t.Errorf("the hub logged %d requests, want 1", n)
	}
}

func TestHub_GitEndpointServesAGitTokenAndCountsFetches(t *testing.T) {
	t.Parallel()
	h := hubtest.New(t)
	repo := h.AddRepository(t, "acme", "demo")
	worker, _ := h.GoodTokens()
	noGit := h.AddToken(hubtest.Token{Permissions: []string{"workspaces:read"}})
	get := func(pat string) *http.Response {
		t.Helper()
		req, _ := http.NewRequest(http.MethodGet, repo.URL()+"/info/refs?service=git-upload-pack", nil)
		if pat != "" {
			req.SetBasicAuth("x", pat)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("GET info/refs: %v", err)
		}
		resp.Body.Close()
		return resp
	}

	for _, pat := range []string{"", noGit} {
		if resp := get(pat); resp.StatusCode != http.StatusUnauthorized || resp.Header.Get("WWW-Authenticate") == "" {
			t.Errorf("with %q: %d, WWW-Authenticate %q; want 401 asking for Basic", pat, resp.StatusCode, resp.Header.Get("WWW-Authenticate"))
		}
	}
	if resp := get(worker); resp.StatusCode != http.StatusOK {
		t.Errorf("with the worker token: %d, want 200", resp.StatusCode)
	}
	if resp, err := http.Get(h.URL() + "/readyz"); err == nil {
		resp.Body.Close()
	}
	repo.SetAnswer(http.StatusServiceUnavailable)
	if resp := get(worker); resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("scripted: %d, want 503", resp.StatusCode)
	}

	fetches := h.Fetches()
	if len(fetches) != 2 || len(fetches[0].Requests) != 3 || len(fetches[1].Requests) != 1 {
		t.Fatalf("fetches %+v, want two: three requests, then one", fetches)
	}
	if fetches[0].Repository != repo.Path() || fetches[0].Requests[2].Basic != worker || fetches[0].Requests[2].Status != http.StatusOK {
		t.Errorf("first fetch %+v, want %s ending in the worker's 200", fetches[0], repo.Path())
	}
}
