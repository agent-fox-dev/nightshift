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
