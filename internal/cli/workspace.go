package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"sync"
	"time"
)

func init() {
	Register(Command{
		Name:     "workspace",
		Synopsis: "workspace <command> [arguments]",
		Summary:  "register and manage the workspaces nightshift works in",
		Setup: func(*flag.FlagSet) func(*Call, []string) int {
			return runWorkspace
		},
	})
}

// workspaceCommand is one command of nightshift workspace.
type workspaceCommand struct {
	// name is the word after "workspace" that selects the command.
	name string
	// synopsis follows "nightshift workspace " in the command's usage.
	synopsis string
	// summary is the line that describes the command in the usage of
	// nightshift workspace.
	summary string
	// setup declares the command's flags on fs and returns the function
	// that runs it with its positional arguments once fs is parsed. Flags
	// are accepted before and after the positional arguments.
	setup func(fs *flag.FlagSet) func(c *Call, args []string) int
}

// workspaceCommands are the commands of nightshift workspace, in the order
// its usage lists them.
var workspaceCommands = []workspaceCommand{
	{
		name:     "add",
		synopsis: "add <slug> --health <command> [--health-timeout <duration>] [--daily-tokens <N>] [--adopt-policy]",
		summary:  "register a workspace once its checks pass",
		setup:    setupWorkspaceAdd,
	},
	{
		name:     "show",
		synopsis: "show <slug>",
		summary:  "show a workspace's state, settings and sensitive paths",
		setup:    setupWorkspaceShow,
	},
	{
		name:     "list",
		synopsis: "list",
		summary:  "list the registered workspaces",
		setup:    setupWorkspaceList,
	},
	{
		name:     "set",
		synopsis: "set <slug> [--health <command>] [--health-timeout <duration>] [--daily-tokens <N>]",
		summary:  "change a workspace's health command, its timeout or its daily limit",
		setup:    setupWorkspaceSet,
	},
	{
		name:     "remove",
		synopsis: "remove <slug>",
		summary:  "remove a workspace from nightshift, leaving it on the hub",
		setup:    setupWorkspaceRemove,
	},
	{
		name:     "protect",
		synopsis: "protect <slug> <glob>... [--block] [--name <rule>]",
		summary:  "mark paths sensitive with a rule at the top of the hub's policy",
		setup:    setupWorkspaceProtect,
	},
	{
		name:     "unprotect",
		synopsis: "unprotect <slug> <rule>",
		summary:  "remove a rule protect wrote from the hub's policy",
		setup:    setupWorkspaceUnprotect,
	},
}

// listColumns are the columns other packages add to the lines of
// workspace list, in the order they were added; listMu guards them.
var (
	listMu      sync.Mutex
	listColumns []listColumn
)

type listColumn struct {
	header string
	value  func(obj map[string]any) string
}

// AddWorkspaceListColumn adds a column to every line of nightshift
// workspace list: fn gives its value from the workspace object the daemon
// answered, and a value that is empty adds nothing to the line. header
// names the column; a package adds its columns from an init function, and
// adding a header twice panics.
func AddWorkspaceListColumn(header string, fn func(obj map[string]any) string) {
	listMu.Lock()
	defer listMu.Unlock()
	for _, col := range listColumns {
		if col.header == header {
			panic("cli: workspace list column " + header + " added twice")
		}
	}
	listColumns = append(listColumns, listColumn{header: header, value: fn})
}

func workspaceListColumns() []listColumn {
	listMu.Lock()
	defer listMu.Unlock()
	return slices.Clone(listColumns)
}

func lookupWorkspaceCommand(name string) (workspaceCommand, bool) {
	for _, wc := range workspaceCommands {
		if wc.name == name {
			return wc, true
		}
	}
	return workspaceCommand{}, false
}

func printWorkspaceUsage(w io.Writer) {
	fmt.Fprintln(w, "usage: nightshift workspace <command> [arguments] [flags]")
	fmt.Fprintln(w, "\ncommands:")
	for _, wc := range workspaceCommands {
		fmt.Fprintf(w, "  %-10s %s\n", wc.name, wc.summary)
	}
}

// runWorkspace selects the command named by args[0] and runs it with the
// rest, the global flags accepted among them too.
func runWorkspace(c *Call, args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(c.Stderr, "nightshift workspace: no command given")
		printWorkspaceUsage(c.Stderr)
		return 2
	}
	wc, ok := lookupWorkspaceCommand(args[0])
	if !ok {
		fmt.Fprintf(c.Stderr, "nightshift workspace: unknown command %q\n", args[0])
		printWorkspaceUsage(c.Stderr)
		return 2
	}

	fs := flag.NewFlagSet("nightshift workspace "+wc.name, flag.ContinueOnError)
	fs.SetOutput(c.Stderr)
	// Empty but for --json, so that only what is given here overrides what
	// the call resolved from the flags before the command's name.
	g := globals{json: c.JSON}
	g.define(fs)
	run := wc.setup(fs)
	fs.Usage = func() {
		fmt.Fprintf(c.Stderr, "usage: nightshift workspace %s\n\n%s\n\nflags:\n", wc.synopsis, wc.summary)
		fs.PrintDefaults()
	}
	positional, err := parseInterleaved(fs, args[1:])
	if err != nil {
		return usageExit(err)
	}
	if g.addr != "" {
		c.addr = g.addr
	}
	if g.tokenFile != "" {
		c.tokenPath = g.tokenFile
	}
	c.JSON = g.json
	c.name, c.usage = "workspace "+wc.name, fs.Usage
	return run(c, positional)
}

// parseInterleaved parses fs's flags wherever they stand among args and
// returns the positional arguments in order. The flag package stops at the
// first positional argument, so the parse goes on after each one; after
// "--" every argument is positional.
func parseInterleaved(fs *flag.FlagSet, args []string) ([]string, error) {
	var positional []string
	for {
		if err := fs.Parse(args); err != nil {
			return nil, err
		}
		rest := fs.Args()
		if consumed := len(args) - len(rest); consumed > 0 && args[consumed-1] == "--" {
			return append(positional, rest...), nil
		}
		if len(rest) == 0 {
			return positional, nil
		}
		positional = append(positional, rest[0])
		args = rest[1:]
	}
}

// sendJSON sends v as the JSON body of a request, as workspaceRequest
// does; a nil v sends no body.
func (c *Call) sendJSON(method, path string, v any) ([]byte, int) {
	if v == nil {
		return c.workspaceRequest(method, path, nil)
	}
	body, err := json.Marshal(v)
	if err != nil {
		fmt.Fprintf(c.Stderr, "nightshift %s: %v\n", c.name, err)
		return nil, 1
	}
	return c.workspaceRequest(method, path, bytes.NewReader(body))
}

// workspaceRequest is Request for the workspace commands, which print an
// error answer's output lines after its message, so that the person reads
// what a refused health command wrote, and exit 2 for a refused glob, as
// for any other command line the person has to correct.
func (c *Call) workspaceRequest(method, path string, body io.Reader) ([]byte, int) {
	a, code := c.send(method, path, body)
	if code != 0 {
		return nil, code
	}
	if a.status >= 400 {
		e := c.reportError(a)
		for _, line := range e.Output {
			fmt.Fprintln(c.Stderr, line)
		}
		if e.ErrorType == "invalid_glob" {
			return nil, 2
		}
		return nil, 1
	}
	return a.body, 0
}

// slugOnly is the slug of a command that takes the slug and nothing else,
// or the usage error's exit code.
func slugOnly(c *Call, args []string) (string, int) {
	switch {
	case len(args) == 0:
		return "", c.UsageError("no slug given")
	case len(args) > 1:
		return "", c.UsageError("unexpected argument %q", args[1])
	}
	return args[0], 0
}

// workspacePath is the route of the workspace slug.
func workspacePath(slug string) string { return "/api/v1/workspaces/" + url.PathEscape(slug) }

// durationFlag defines the flag name on fs, a Go duration, whose text is
// sent as it was given once it parses.
func durationFlag(fs *flag.FlagSet, name, usage string) *string {
	var text string
	fs.Func(name, usage, func(v string) error {
		if _, err := time.ParseDuration(v); err != nil {
			return errors.New("not a duration, such as 30m")
		}
		text = v
		return nil
	})
	return &text
}

func setupWorkspaceAdd(fs *flag.FlagSet) func(*Call, []string) int {
	health := fs.String("health", "", "the health `command`, which must exit 0 on the default branch")
	timeout := durationFlag(fs, "health-timeout", "how long the health command may run, a Go `duration` (default 30m)")
	tokens := fs.Int64("daily-tokens", 0, "the workspace's daily token `limit` (default the daemon's)")
	adopt := fs.Bool("adopt-policy", false, "write nightshift's three checks into a policy that lacks them")
	return func(c *Call, args []string) int {
		slug, code := slugOnly(c, args)
		switch {
		case code != 0:
			return code
		case *health == "":
			return c.UsageError("--health is required")
		}
		body := map[string]any{"slug": slug, "health_command": *health, "adopt_policy": *adopt}
		// The timeout and the limit are sent only where given, so that the
		// daemon's defaults apply otherwise.
		fs.Visit(func(f *flag.Flag) {
			switch f.Name {
			case "health-timeout":
				body["health_timeout"] = *timeout
			case "daily-tokens":
				body["daily_tokens"] = *tokens
			}
		})
		// The registration runs the health command, which may take as long
		// as its timeout: the request is sent without a deadline.
		answer, code := c.sendJSON(http.MethodPost, "/api/v1/workspaces", body)
		if code != 0 {
			return code
		}
		if c.JSON {
			c.Stdout.Write(answer)
			return 0
		}
		var registered struct {
			PolicyRevision *int    `json:"policy_revision"`
			Warning        *string `json:"warning"`
		}
		if err := json.Unmarshal(answer, &registered); err != nil {
			fmt.Fprintf(c.Stderr, "nightshiftd answered a registration that is not JSON: %v\n", err)
			return 1
		}
		fmt.Fprintf(c.Stdout, "registered %s\n", slug)
		if registered.PolicyRevision != nil {
			fmt.Fprintf(c.Stdout, "policy adopted at revision %d\n", *registered.PolicyRevision)
		}
		if registered.Warning != nil {
			fmt.Fprintln(c.Stderr, *registered.Warning)
		}
		return 0
	}
}

// shownWorkspace is the workspace object as show and list print it.
type shownWorkspace struct {
	Slug            string `json:"slug"`
	State           string `json:"state"`
	HealthCommand   string `json:"health_command"`
	HealthTimeout   string `json:"health_timeout"`
	DailyTokens     int64  `json:"daily_tokens"`
	TokensUsedToday int64  `json:"tokens_used_today"`
	SensitiveRules  []struct {
		Name      string   `json:"name"`
		Paths     []string `json:"paths"`
		Approvals int      `json:"approvals"`
		Block     bool     `json:"block"`
	} `json:"sensitive_rules"`
}

// printAnswer prints the daemon's answer as it came with --json, or decodes
// it into v, the answer's what for an error; it returns the exit code, and
// whether the command prints its text next.
func (c *Call) printAnswer(answer []byte, v any, what string) (int, bool) {
	if c.JSON {
		c.Stdout.Write(answer)
		return 0, false
	}
	if err := json.Unmarshal(answer, v); err != nil {
		fmt.Fprintf(c.Stderr, "nightshiftd answered %s that is not JSON: %v\n", what, err)
		return 1, false
	}
	return 0, true
}

func setupWorkspaceShow(*flag.FlagSet) func(*Call, []string) int {
	return func(c *Call, args []string) int {
		slug, code := slugOnly(c, args)
		if code != 0 {
			return code
		}
		answer, code := c.sendJSON(http.MethodGet, workspacePath(slug), nil)
		if code != 0 {
			return code
		}
		var ws shownWorkspace
		if code, text := c.printAnswer(answer, &ws, "a workspace"); !text {
			return code
		}
		fmt.Fprintf(c.Stdout, "%s: %s\n", ws.Slug, ws.State)
		fmt.Fprintf(c.Stdout, "health: %s (timeout %s)\n", ws.HealthCommand, ws.HealthTimeout)
		fmt.Fprintf(c.Stdout, "daily limit: %d tokens, %d used today\n", ws.DailyTokens, ws.TokensUsedToday)
		if len(ws.SensitiveRules) == 0 {
			fmt.Fprintln(c.Stdout, "sensitive paths: none")
			return 0
		}
		fmt.Fprintln(c.Stdout, "sensitive paths:")
		for _, rule := range ws.SensitiveRules {
			kind := "approval"
			if rule.Block {
				kind = "block"
			}
			paths := strings.Join(rule.Paths, ", ")
			// The default applies to every path no rule matches.
			if rule.Name == "default" {
				paths = "every other path"
			}
			fmt.Fprintf(c.Stdout, "  %s %s: %s\n", rule.Name, kind, paths)
		}
		return 0
	}
}

func setupWorkspaceList(*flag.FlagSet) func(*Call, []string) int {
	return func(c *Call, args []string) int {
		if len(args) > 0 {
			return c.UsageError("unexpected argument %q", args[0])
		}
		answer, code := c.sendJSON(http.MethodGet, "/api/v1/workspaces", nil)
		if code != 0 {
			return code
		}
		var list struct {
			Workspaces []json.RawMessage `json:"workspaces"`
		}
		if code, text := c.printAnswer(answer, &list, "a list of workspaces"); !text {
			return code
		}
		if len(list.Workspaces) == 0 {
			fmt.Fprintln(c.Stdout, "no workspaces")
			return 0
		}
		columns := workspaceListColumns()
		for _, raw := range list.Workspaces {
			var ws shownWorkspace
			var obj map[string]any
			if json.Unmarshal(raw, &ws) != nil || json.Unmarshal(raw, &obj) != nil {
				fmt.Fprintf(c.Stderr, "nightshiftd answered a workspace that is not a JSON object: %s\n", raw)
				return 1
			}
			line := fmt.Sprintf("%s  %s  %d/%d tokens  health: %s (%s)",
				ws.Slug, ws.State, ws.TokensUsedToday, ws.DailyTokens, ws.HealthCommand, ws.HealthTimeout)
			for _, col := range columns {
				if v := col.value(obj); v != "" {
					line += "  " + v
				}
			}
			fmt.Fprintln(c.Stdout, line)
		}
		return 0
	}
}

func setupWorkspaceSet(fs *flag.FlagSet) func(*Call, []string) int {
	health := fs.String("health", "", "the new health `command`")
	timeout := durationFlag(fs, "health-timeout", "the new health command's timeout, a Go `duration`")
	tokens := fs.Int64("daily-tokens", 0, "the new daily token `limit`")
	return func(c *Call, args []string) int {
		slug, code := slugOnly(c, args)
		if code != 0 {
			return code
		}
		// Only what was given is sent, so that the rest stays as stored.
		body := map[string]any{}
		fs.Visit(func(f *flag.Flag) {
			switch f.Name {
			case "health":
				body["health_command"] = *health
			case "health-timeout":
				body["health_timeout"] = *timeout
			case "daily-tokens":
				body["daily_tokens"] = *tokens
			}
		})
		if len(body) == 0 {
			return c.UsageError("give one or more of --health, --health-timeout and --daily-tokens")
		}
		answer, code := c.sendJSON(http.MethodPatch, workspacePath(slug), body)
		if code != 0 {
			return code
		}
		if c.JSON {
			c.Stdout.Write(answer)
			return 0
		}
		fmt.Fprintf(c.Stdout, "updated %s\n", slug)
		return 0
	}
}

func setupWorkspaceRemove(*flag.FlagSet) func(*Call, []string) int {
	return func(c *Call, args []string) int {
		slug, code := slugOnly(c, args)
		if code != 0 {
			return code
		}
		if _, code := c.sendJSON(http.MethodDelete, workspacePath(slug), nil); code != 0 {
			return code
		}
		// A removal answers no body, so --json prints nothing.
		if !c.JSON {
			fmt.Fprintf(c.Stdout, "removed %s\n", slug)
		}
		return 0
	}
}

// rulesPath is the route of the rules of the workspace slug's policy.
func rulesPath(slug string) string { return workspacePath(slug) + "/policy/rules" }

func setupWorkspaceProtect(fs *flag.FlagSet) func(*Call, []string) int {
	block := fs.Bool("block", false, "refuse every change of the paths instead of waiting for your approval")
	name := fs.String("name", "", "the rule's `name` (default nightshift-<n>, the next free number)")
	return func(c *Call, args []string) int {
		switch {
		case len(args) == 0:
			return c.UsageError("no slug given")
		case len(args) == 1:
			return c.UsageError("no glob given")
		}
		slug := args[0]
		body := map[string]any{"globs": args[1:], "block": *block}
		if *name != "" {
			body["name"] = *name
		}
		answer, code := c.sendJSON(http.MethodPost, rulesPath(slug), body)
		if code != 0 {
			return code
		}
		var written struct {
			Rule struct {
				Name    string   `json:"name"`
				Paths   []string `json:"paths"`
				Require struct {
					Block bool `json:"block"`
				} `json:"require"`
			} `json:"rule"`
			Revision int `json:"revision"`
		}
		if code, text := c.printAnswer(answer, &written, "a rule"); !text {
			return code
		}
		kind := "approval"
		if written.Rule.Require.Block {
			kind = "block"
		}
		fmt.Fprintf(c.Stdout, "rule %s (%s): %s\n", written.Rule.Name, kind, strings.Join(written.Rule.Paths, ", "))
		fmt.Fprintf(c.Stdout, "policy revision %d\n", written.Revision)
		return 0
	}
}

func setupWorkspaceUnprotect(*flag.FlagSet) func(*Call, []string) int {
	return func(c *Call, args []string) int {
		switch {
		case len(args) == 0:
			return c.UsageError("no slug given")
		case len(args) == 1:
			return c.UsageError("no rule given")
		case len(args) > 2:
			return c.UsageError("unexpected argument %q", args[2])
		}
		slug, rule := args[0], args[1]
		answer, code := c.sendJSON(http.MethodDelete, rulesPath(slug)+"/"+url.PathEscape(rule), nil)
		if code != 0 {
			return code
		}
		var removed struct {
			Revision int `json:"revision"`
		}
		if code, text := c.printAnswer(answer, &removed, "a removal"); !text {
			return code
		}
		fmt.Fprintf(c.Stdout, "removed rule %s\npolicy revision %d\n", rule, removed.Revision)
		return 0
	}
}
