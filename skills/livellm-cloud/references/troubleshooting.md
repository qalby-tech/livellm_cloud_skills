# Troubleshooting

Every command prints JSON. Errors carry `error` and `next`; `next` is the thing
to do. The exit code says how bad it is: 2 the user must act, 3 not ready yet,
4 busy, 1 anything else.

## By answer

**not signed in / 401.** There is no sign-in, or it ended (the user signed the
agent out, or it went unused for 30 days). Run `login` and give the user the
link. Never ask for an API key in chat.

**402, the plan is full.** Creating this would go past the plan. Stop, show
usage from `whoami`, and let the user decide. Deleting something to make room is
never your call.

**403.** Either the resource belongs to someone else, or the agent's access
doesn't stretch this far. Say which: "this agent can use and create, but this is
your resource; allow full access or do it in the console".

**404.** The id doesn't exist in this workspace. Run `ls`; ids are often close
but not exact.

**409.** Something else is changing the resource, often a build. Wait a few
seconds and try once more.

**422.** A value was refused, and the message names it. Fix that value. Never
send the same request again hoping for a different answer.

**5xx or "cannot reach LiveLLM".** The platform or the network. Retry twice with
a pause. If it keeps failing, tell the user plainly, with the time and what you
were doing.

## By symptom

**A resource stays `starting`.** Machines take minutes on first boot, Windows
longer. Use `wait` with a longer timeout. If it times out, tell the user what
the status said instead of guessing.

**A browser address refuses a connection.** The connect token lives 15 minutes.
Run `connect` again and use the fresh address. A session that is already open
keeps working.

**A build fails.** `progress <id>` names the stage and shows the error. Fix the
repository and run `build <id>`. If the new build is worse than the old one,
`builds <id>` then `deploy <id> <build> --yes`.

**A public address answers 404 or 502.** The app may still be starting, or the
port in its settings doesn't match what the program listens on.

**Something runs but misbehaves.** `logs <id>` prints the last log lines and
each container's state, restarts and resource use. Read it before guessing, and
quote the line that explains it rather than summarising vaguely.

**A password doesn't work.** Database, machine and port passwords are set once
and can't be read back. The user can set a new one; every app using it then
needs the new value.

**The user says an agent is doing something they didn't ask for.** Tell them
about the console's Agents page: signing an agent out stops it immediately.

## Habits

- Say what you did, what exists now, and what it costs.
- When you're unsure whether something can be undone, ask first.
- Never invent an id, an address or a password. Read them from the commands.
- Keep secrets out of chat, except the one-time handover the user needs.
