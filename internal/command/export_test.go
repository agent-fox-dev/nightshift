package command

// OnGroupSignal makes r call f with the group's id each time it is about
// to signal a command's process group.
func OnGroupSignal(r *Runner, f func(pgid int)) { r.beforeSignal = f }
