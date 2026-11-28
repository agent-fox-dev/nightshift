package cli_test

import (
	"encoding/json"
	"net/http"
	"reflect"
	"testing"
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
