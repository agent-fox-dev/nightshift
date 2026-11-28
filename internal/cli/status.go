package cli

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
)

func init() {
	Register(Command{
		Name:     "status",
		Synopsis: "status",
		Summary:  "show the daemon's version and whether it reaches the hub",
		Setup: func(*flag.FlagSet) func(*Call, []string) int {
			return runStatus
		},
	})
}

func runStatus(c *Call, args []string) int {
	if len(args) > 0 {
		return c.UsageError("unexpected argument %q", args[0])
	}
	body, code := c.Request(http.MethodGet, "/api/v1/status", nil)
	if code != 0 {
		return code
	}
	if c.JSON {
		c.Stdout.Write(body)
		return 0
	}
	var st struct {
		Version string `json:"version"`
		Hub     struct {
			Endpoint         string  `json:"endpoint"`
			Reachable        bool    `json:"reachable"`
			UnreachableSince *string `json:"unreachable_since"`
			CredentialError  *string `json:"credential_error"`
		} `json:"hub"`
		Workspaces []struct {
			Slug            string `json:"slug"`
			State           string `json:"state"`
			TokensUsedToday int64  `json:"tokens_used_today"`
		} `json:"workspaces"`
	}
	if err := json.Unmarshal(body, &st); err != nil {
		fmt.Fprintf(c.Stderr, "nightshiftd answered a status that is not JSON: %v\n", err)
		return 1
	}
	fmt.Fprintf(c.Stdout, "nightshiftd %s\n", st.Version)
	switch {
	case st.Hub.Reachable:
		fmt.Fprintf(c.Stdout, "hub: %s reachable\n", st.Hub.Endpoint)
	case st.Hub.UnreachableSince != nil:
		fmt.Fprintf(c.Stdout, "hub: unreachable since %s\n", *st.Hub.UnreachableSince)
	default:
		fmt.Fprintf(c.Stdout, "hub: %s unreachable\n", st.Hub.Endpoint)
	}
	if st.Hub.CredentialError != nil {
		fmt.Fprintln(c.Stdout, *st.Hub.CredentialError)
	}
	for _, w := range st.Workspaces {
		fmt.Fprintf(c.Stdout, "workspace %s: %s, %d tokens today\n", w.Slug, w.State, w.TokensUsedToday)
	}
	return 0
}
