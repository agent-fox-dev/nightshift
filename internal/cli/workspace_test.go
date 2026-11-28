package cli_test

import (
	"encoding/json"
	"net/http"
	"reflect"
	"sync"
	"testing"

	"github.com/agent-fox-dev/nightshift/internal/cli"
)

const (
	registeredBody = `{"slug":"demo","state":"green","health_command":"make check","health_timeout":"30m0s",` +
		`"daily_tokens":50000000,"tokens_used_today":0,"created_at":"2026-11-28T12:00:00Z",`
	warningLine = "no path of demo is marked sensitive; every change lands without you"
)

func TestWorkspaceAdd_SendsTheRegistrationAndPrintsTheAnswer(t *testing.T) {
	t.Parallel()
	const (
		nothingAdopted = registeredBody + `"policy_revision":null,"warning":null}`
		adopted        = registeredBody + `"policy_revision":3,"warning":"` + warningLine + `"}`
	)
	tests := []struct {
		name       string
		args       []string
		served     string
		wantBody   map[string]any
		wantStdout string
		wantStderr string
		// json compares stdout with the served body as JSON.
		json bool
	}{
		{
			name:       "only --health",
			args:       []string{"workspace", "add", "demo", "--health", "make check"},
			served:     nothingAdopted,
			wantBody:   map[string]any{"slug": "demo", "health_command": "make check", "adopt_policy": false},
			wantStdout: "registered demo\n",
		},
		{
			name: "every flag, before and after the slug",
			args: []string{"workspace", "add", "--health-timeout", "45m", "demo", "--health", "make check",
				"--daily-tokens", "9", "--adopt-policy"},
			served: adopted,
			wantBody: map[string]any{"slug": "demo", "health_command": "make check", "adopt_policy": true,
				"health_timeout": "45m", "daily_tokens": float64(9)},
			wantStdout: "registered demo\npolicy adopted at revision 3\n",
			wantStderr: warningLine + "\n",
		},
		{
			name:     "json",
			args:     []string{"workspace", "--json", "add", "demo", "--health", "make check"},
			served:   adopted,
			wantBody: map[string]any{"slug": "demo", "health_command": "make check", "adopt_policy": false},
			json:     true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			d := newFakeDaemon(t, http.StatusCreated, tt.served)
			c := newClient(t)

			code, stdout, stderr := c.run(append(tt.args, "--addr", d.addr())...)

			if code != 0 {
				t.Fatalf("exit code = %d, want 0 (stderr %q)", code, stderr)
			}
			if tt.json {
				var got, want any
				if err := json.Unmarshal([]byte(stdout), &got); err != nil {
					t.Fatalf("stdout %q is not JSON: %v", stdout, err)
				}
				json.Unmarshal([]byte(tt.served), &want)
				if !reflect.DeepEqual(got, want) {
					t.Errorf("stdout = %v, want the served %v", got, want)
				}
			} else if stdout != tt.wantStdout {
				t.Errorf("stdout = %q, want %q", stdout, tt.wantStdout)
			}
			if stderr != tt.wantStderr {
				t.Errorf("stderr = %q, want %q", stderr, tt.wantStderr)
			}
			got := d.seen()
			if len(got) != 1 || got[0].method != http.MethodPost || got[0].path != "/api/v1/workspaces" {
				t.Fatalf("the daemon saw %+v, want one POST /api/v1/workspaces", got)
			}
			var body map[string]any
			if err := json.Unmarshal([]byte(got[0].body), &body); err != nil {
				t.Fatalf("the request's body %q is not JSON: %v", got[0].body, err)
			}
			if !reflect.DeepEqual(body, tt.wantBody) {
				t.Errorf("the request's body = %v, want %v", body, tt.wantBody)
			}
		})
	}
}

const shownHead = "demo: green\nhealth: make check (timeout 30m0s)\ndaily limit: 50000000 tokens, 0 used today\n"

func TestWorkspaceShow_PrintsTheWorkspaceAndItsSensitivePaths(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name       string
		rules      string
		json       bool
		wantStdout string
	}{
		{
			name:       "no sensitive rule",
			rules:      `[]`,
			wantStdout: shownHead + "sensitive paths: none\n",
		},
		{
			name: "an approval, a block and the default",
			rules: `[{"name":"nightshift-1","paths":["deploy/**","ops/*.yaml"],"approvals":1,"block":false},` +
				`{"name":"secrets","paths":["deploy/secrets/**"],"approvals":0,"block":true},` +
				`{"name":"default","paths":[],"approvals":1,"block":false}]`,
			wantStdout: shownHead + "sensitive paths:\n" +
				"  nightshift-1 approval: deploy/**, ops/*.yaml\n" +
				"  secrets block: deploy/secrets/**\n" +
				"  default approval: every other path\n",
		},
		{
			name:  "json",
			rules: `[]`,
			json:  true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			served := `{"slug":"demo","state":"green","health_command":"make check","health_timeout":"30m0s",` +
				`"daily_tokens":50000000,"tokens_used_today":0,"created_at":"2026-11-28T12:00:00Z","sensitive_rules":` + tt.rules + `}`
			d := newFakeDaemon(t, http.StatusOK, served)
			args := []string{"workspace", "show", "demo", "--addr", d.addr()}
			if tt.json {
				args = append(args, "--json")
			}

			code, stdout, stderr := newClient(t).run(args...)

			if code != 0 {
				t.Fatalf("exit code = %d, want 0 (stderr %q)", code, stderr)
			}
			if tt.json {
				assertSameJSON(t, stdout, served)
			} else if stdout != tt.wantStdout {
				t.Errorf("stdout = %q, want %q", stdout, tt.wantStdout)
			}
			if got := d.seen(); len(got) != 1 || got[0].method != http.MethodGet || got[0].path != "/api/v1/workspaces/demo" {
				t.Errorf("the daemon saw %+v, want one GET /api/v1/workspaces/demo", got)
			}
		})
	}
}

// pausedColumn registers, once per test process, a column of `workspace
// list` that marks a workspace whose paused member is true, as another
// package would.
var pausedColumn sync.Once

func TestWorkspaceList_PrintsOneLinePerWorkspace(t *testing.T) {
	t.Parallel()
	pausedColumn.Do(func() {
		cli.AddWorkspaceListColumn("paused", func(obj map[string]any) string {
			if obj["paused"] == true {
				return "paused"
			}
			return ""
		})
	})
	const (
		alpha = `{"slug":"alpha","state":"green","health_command":"make check","health_timeout":"30m0s",` +
			`"daily_tokens":50000000,"tokens_used_today":0,"created_at":"2026-11-28T12:00:00Z"`
		zeta = `{"slug":"zeta","state":"red","health_command":"true","health_timeout":"1m0s",` +
			`"daily_tokens":100,"tokens_used_today":42,"created_at":"2026-11-28T12:00:00Z"`
		alphaLine = "alpha  green  0/50000000 tokens  health: make check (30m0s)"
		zetaLine  = "zeta  red  42/100 tokens  health: true (1m0s)"
	)
	tests := []struct {
		name       string
		served     string
		json       bool
		wantStdout string
	}{
		{
			name:       "two workspaces",
			served:     `{"workspaces":[` + alpha + `},` + zeta + `}]}`,
			wantStdout: alphaLine + "\n" + zetaLine + "\n",
		},
		{
			name:       "none",
			served:     `{"workspaces":[]}`,
			wantStdout: "no workspaces\n",
		},
		{
			name:       "a registered column",
			served:     `{"workspaces":[` + alpha + `,"paused":true},` + zeta + `,"paused":false}]}`,
			wantStdout: alphaLine + "  paused\n" + zetaLine + "\n",
		},
		{
			name:   "json",
			served: `{"workspaces":[` + alpha + `}]}`,
			json:   true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			d := newFakeDaemon(t, http.StatusOK, tt.served)
			args := []string{"workspace", "list", "--addr", d.addr()}
			if tt.json {
				args = append(args, "--json")
			}

			code, stdout, stderr := newClient(t).run(args...)

			if code != 0 {
				t.Fatalf("exit code = %d, want 0 (stderr %q)", code, stderr)
			}
			if tt.json {
				assertSameJSON(t, stdout, tt.served)
			} else if stdout != tt.wantStdout {
				t.Errorf("stdout = %q, want %q", stdout, tt.wantStdout)
			}
			if got := d.seen(); len(got) != 1 || got[0].method != http.MethodGet || got[0].path != "/api/v1/workspaces" {
				t.Errorf("the daemon saw %+v, want one GET /api/v1/workspaces", got)
			}
		})
	}
}

func TestWorkspaceSetAndRemove_SendTheChangeAndPrintIt(t *testing.T) {
	t.Parallel()
	const changed = `{"slug":"demo","state":"green","health_command":"true","health_timeout":"45m0s",` +
		`"daily_tokens":9,"tokens_used_today":0,"created_at":"2026-11-28T12:00:00Z"}`
	tests := []struct {
		name       string
		args       []string
		status     int
		served     string
		wantMethod string
		// wantBody is the request's body as JSON; nil for no body.
		wantBody   map[string]any
		wantStdout string
	}{
		{
			name:       "set the limit",
			args:       []string{"workspace", "set", "demo", "--daily-tokens", "9"},
			status:     http.StatusOK,
			served:     changed,
			wantMethod: http.MethodPatch,
			wantBody:   map[string]any{"daily_tokens": float64(9)},
			wantStdout: "updated demo\n",
		},
		{
			name: "set all three, before and after the slug",
			args: []string{"workspace", "set", "--health", "true", "demo", "--health-timeout", "45m",
				"--daily-tokens", "9"},
			status:     http.StatusOK,
			served:     changed,
			wantMethod: http.MethodPatch,
			wantBody:   map[string]any{"health_command": "true", "health_timeout": "45m", "daily_tokens": float64(9)},
			wantStdout: "updated demo\n",
		},
		{
			name:       "set with json",
			args:       []string{"workspace", "set", "demo", "--daily-tokens", "9", "--json"},
			status:     http.StatusOK,
			served:     changed,
			wantMethod: http.MethodPatch,
			wantBody:   map[string]any{"daily_tokens": float64(9)},
			wantStdout: changed,
		},
		{
			name:       "remove",
			args:       []string{"workspace", "remove", "demo"},
			status:     http.StatusNoContent,
			wantMethod: http.MethodDelete,
			wantStdout: "removed demo\n",
		},
		{
			name:       "remove with json",
			args:       []string{"workspace", "remove", "demo", "--json"},
			status:     http.StatusNoContent,
			wantMethod: http.MethodDelete,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			d := newFakeDaemon(t, tt.status, tt.served)

			code, stdout, stderr := newClient(t).run(append(tt.args, "--addr", d.addr())...)

			if code != 0 {
				t.Fatalf("exit code = %d, want 0 (stderr %q)", code, stderr)
			}
			if stdout != tt.wantStdout {
				t.Errorf("stdout = %q, want %q", stdout, tt.wantStdout)
			}
			if stderr != "" {
				t.Errorf("stderr = %q, want nothing", stderr)
			}
			got := d.seen()
			if len(got) != 1 || got[0].method != tt.wantMethod || got[0].path != "/api/v1/workspaces/demo" {
				t.Fatalf("the daemon saw %+v, want one %s /api/v1/workspaces/demo", got, tt.wantMethod)
			}
			if tt.wantBody == nil {
				if got[0].body != "" {
					t.Errorf("the request's body = %q, want none", got[0].body)
				}
				return
			}
			var body map[string]any
			if err := json.Unmarshal([]byte(got[0].body), &body); err != nil {
				t.Fatalf("the request's body %q is not JSON: %v", got[0].body, err)
			}
			if !reflect.DeepEqual(body, tt.wantBody) {
				t.Errorf("the request's body = %v, want %v", body, tt.wantBody)
			}
		})
	}
}

// assertSameJSON fails the test unless stdout is the JSON of served.
func assertSameJSON(t *testing.T, stdout, served string) {
	t.Helper()
	var got, want any
	if err := json.Unmarshal([]byte(stdout), &got); err != nil {
		t.Fatalf("stdout %q is not JSON: %v", stdout, err)
	}
	json.Unmarshal([]byte(served), &want)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("stdout = %v, want the served %v", got, want)
	}
}
