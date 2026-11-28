package cli

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
)

// httpClient sends every request to the daemon. It goes direct, never
// through a proxy from the environment, because the requests carry the
// client token.
var httpClient = func() *http.Client {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.Proxy = nil
	return &http.Client{Transport: t}
}()

// Call is one invocation of a command: where its output goes, whether it
// prints JSON, and the daemon it sends its requests to.
type Call struct {
	Stdout, Stderr io.Writer
	// JSON is the --json flag: print the daemon's answer as it came.
	JSON bool

	name      string
	usage     func()
	addr      string
	tokenPath string
}

// Addr is the host:port of the daemon the call's requests go to.
func (c *Call) Addr() string { return c.addr }

// UsageError prints the reason and the command's usage, and returns the
// exit code of a usage error, for a command whose arguments are wrong.
func (c *Call) UsageError(format string, args ...any) int {
	fmt.Fprintf(c.Stderr, "nightshift %s: %s\n", c.name, fmt.Sprintf(format, args...))
	c.usage()
	return 2
}

// Request sends a request for path, which starts with "/api/v1/", to the
// daemon with the client token, and returns the answer's body and 0 on an
// answer below 400. Any other outcome is printed here and its exit code returned,
// which the command returns as it is: 1 with "cannot read the client token
// at <path>" before any request, 1 with "cannot reach nightshiftd at
// <address>", and 1 with the error body's message for a 4xx or 5xx answer,
// the body itself on stdout with --json. A body that is not nil is sent as
// JSON.
func (c *Call) Request(method, path string, body io.Reader) ([]byte, int) {
	a, code := c.send(method, path, body)
	if code != 0 {
		return nil, code
	}
	if a.status >= 400 {
		c.reportError(a)
		return nil, 1
	}
	return a.body, 0
}

// reply is the daemon's answer to a request.
type reply struct {
	status int
	// statusLine is the status as the answer gave it, such as "404 Not
	// Found".
	statusLine string
	body       []byte
}

// errorBody is the body of an error answer of the daemon.
type errorBody struct {
	ErrorType string `json:"error_type"`
	Message   string `json:"message"`
	// Output is the last lines of a command's output, which a refusal
	// caused by the command carries.
	Output []string `json:"output"`
}

// send sends the request as Request does and returns the daemon's answer,
// whatever its status. A request that gets no answer is printed here, and
// its exit code returned.
func (c *Call) send(method, path string, body io.Reader) (reply, int) {
	content, err := os.ReadFile(c.tokenPath)
	token := strings.TrimSpace(string(content))
	if err != nil || token == "" {
		fmt.Fprintf(c.Stderr, "cannot read the client token at %s\n", c.tokenPath)
		return reply{}, 1
	}
	req, err := http.NewRequest(method, "http://"+c.addr+path, body)
	if err != nil {
		fmt.Fprintf(c.Stderr, "cannot reach nightshiftd at %s\n", c.addr)
		return reply{}, 1
	}
	req.Header.Set("Authorization", "Bearer "+token)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		fmt.Fprintf(c.Stderr, "cannot reach nightshiftd at %s\n", c.addr)
		return reply{}, 1
	}
	defer resp.Body.Close()
	content, err = io.ReadAll(resp.Body)
	if err != nil {
		fmt.Fprintf(c.Stderr, "cannot reach nightshiftd at %s\n", c.addr)
		return reply{}, 1
	}
	return reply{status: resp.StatusCode, statusLine: resp.Status, body: content}, 0
}

// reportError prints the error answer a as Request does, its body on
// stdout with --json and its message on stderr, and returns its body.
func (c *Call) reportError(a reply) errorBody {
	if c.JSON {
		c.Stdout.Write(a.body)
	}
	var e errorBody
	if json.Unmarshal(a.body, &e) != nil || e.Message == "" {
		e.Message = "nightshiftd answered " + a.statusLine
	}
	fmt.Fprintln(c.Stderr, e.Message)
	return e
}
