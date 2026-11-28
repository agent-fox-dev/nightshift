package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
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

// sendJSON sends v as the JSON body of a request, as Request does.
func (c *Call) sendJSON(method, path string, v any) ([]byte, int) {
	body, err := json.Marshal(v)
	if err != nil {
		fmt.Fprintf(c.Stderr, "nightshift %s: %v\n", c.name, err)
		return nil, 1
	}
	return c.Request(method, path, bytes.NewReader(body))
}

func setupWorkspaceAdd(fs *flag.FlagSet) func(*Call, []string) int {
	health := fs.String("health", "", "the health `command`, which must exit 0 on the default branch")
	var timeout string
	fs.Func("health-timeout", "how long the health command may run, a Go `duration` (default 30m)", func(v string) error {
		if _, err := time.ParseDuration(v); err != nil {
			return errors.New("not a duration, such as 30m")
		}
		timeout = v
		return nil
	})
	tokens := fs.Int64("daily-tokens", 0, "the workspace's daily token `limit` (default the daemon's)")
	adopt := fs.Bool("adopt-policy", false, "write nightshift's three checks into a policy that lacks them")
	return func(c *Call, args []string) int {
		switch {
		case len(args) == 0:
			return c.UsageError("no slug given")
		case len(args) > 1:
			return c.UsageError("unexpected argument %q", args[1])
		case *health == "":
			return c.UsageError("--health is required")
		}
		body := map[string]any{"slug": args[0], "health_command": *health, "adopt_policy": *adopt}
		// The timeout and the limit are sent only where given, so that the
		// daemon's defaults apply otherwise.
		fs.Visit(func(f *flag.Flag) {
			switch f.Name {
			case "health-timeout":
				body["health_timeout"] = timeout
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
		fmt.Fprintf(c.Stdout, "registered %s\n", args[0])
		if registered.PolicyRevision != nil {
			fmt.Fprintf(c.Stdout, "policy adopted at revision %d\n", *registered.PolicyRevision)
		}
		if registered.Warning != nil {
			fmt.Fprintln(c.Stderr, *registered.Warning)
		}
		return 0
	}
}
