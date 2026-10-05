# Troubleshooting

Every command prints JSON. Errors carry `error` and `next`; `next` is the thing
to do. The exit code says how bad it is: 2 the user must act, 3 not ready yet,
4 busy, 1 anything else.

## By answer

**not signed in / 401.** There is no sign-in, or it ended (the user signed the
agent out, or it went unused for 30 days). Run `login`, give the user the link,
and run `login` again once they have pressed Allow. "still waiting" (exit 3)
means they haven't yet. Never ask for an API key in chat.

**402, the plan is full.** Creating this would go past the plan. Stop, show
usage from `whoami`, and let the user decide. Deleting something to make room is
never your call.

**403.** Either the resource belongs to someone else, or the agent lacks the
permission. Say which: "this agent can't run commands; turn on Run commands for
it on the Agents page", or "this is your resource; turn on Manage everything
for this agent, or do it in the console". A 403 naming Network ("can't let web
reach db inside the workspace") means letting one resource reach another:
tell the user what would reach what and why; they turn on Network (Agents
page, or Keys page for an API key) or make the opening in the console:
Reachable from on the resource, or, for a database (it has none), a link on
what uses it (`references/inside-access.md`).

**404.** The id doesn't exist in this workspace. Run `ls`; ids are often close
but not exact.

**409.** Another agent is working on the machine: the message names it and
until when. Wait until then, or use another machine; never ask the user to
release it for you unless they want to. Otherwise something else is changing
the resource, often a build: wait a few seconds and try once more. A
database "still starting, so its location can't change" is different: its
first start never finished, and retrying won't help. Ask the user before
deleting it and creating it again.

**422.** A value was refused, and the message names it. Fix that value. Never
send the same request again hoping for a different answer.

**5xx or "cannot reach LiveLLM".** The platform or the network. Retry twice with
a pause. If it keeps failing, tell the user plainly, with the time and what you
were doing.

## By symptom

**A resource stays `starting`.** Machines take minutes on first boot, Windows
longer. Use `wait` with a longer timeout. If it times out, tell the user what
the status said instead of guessing.

**A resource with a location stays `starting`.** The status says why.
"Waiting for room on host H (memory)" or "in region R": that host or region
has no room for it, and it still counts toward the plan while it waits.
"Its host H isn't available; choose another location": the host it is pinned
to is down or gone. Tell the user and let them choose: another place (`hosts`
shows the free room), automatic (`set` with the block's `"placement": null`),
or a smaller size.

**A browser address refuses a connection.** The connect token lives 15 minutes.
Run `connect` again and use the fresh address. A session that is already open
keeps working.

**A browser refuses an engine.** 422 `engine_unavailable`: this LiveLLM
doesn't offer Camoufox (`llc.py engines`); tell the user. `engine_fixed`: a
browser's engine can't change; make a new one and, once the user agrees, add
the cookies (rule 10). `extensions_unsupported`: Camoufox takes no extensions.
`engine_mismatch`: a profile copies only between browsers of one engine; add
the cookies instead, once the user agrees. `profile_engine` (or
`not_livellm_profile` naming Chrome): a profile from the other engine; add its
cookies instead, once the user agrees. A Browser API has no engine: it holds
both.

**A Camoufox browser's address refuses Playwright (428).** The installed
Playwright isn't the one `playwright.version` names (1.62). Install that one
(`assets/playwright_connect.py` prints the line). `cdp_connect` saying "This
browser runs Camoufox" means use `playwright_connect` instead. A
`browser.new_page()` that hangs or a page at the wrong size: open pages in
`contexts[0]`, and give a context of your own `no_viewport=True`.

**A Camoufox browser looks like a new device to a site.** An update to a new
Firefox major version draws a new fingerprint. Ask the user to sign in again
on the live view if the site asks.

**A browser's pages stop loading after a proxy change.** `proxy show ID`:
`mode: waiting` means the settings haven't reached it yet (a few seconds);
`lastError: upstream_unreachable` or `upstream_login_refused` means the proxy
itself refuses, and nothing goes out direct in the meantime. Tell the user
which proxy (`via`), and let them fix it or `proxy clear`. A 429 on rotate is a
mobile proxy asked for a new IP too soon: wait its minimum time.
`change_ip_failed` on every rotation: check whether the provider wants its
change-IP address called with `GET` or `POST` (`changeIpMethod`).

**A profile action answers 409 "Restart this browser once".** The browser was
made before profiles: ask the user, then `restart ID --yes`. A 409 "The browser's
profiles are still starting" needs no restart: wait half a minute and try again. A 507 means its
storage is full: the user grows it or deletes a snapshot.

**A build fails.** `progress <id>` names the stage and shows the error. Fix the
repository and run `build <id>`. If the new build is worse than the old one,
`builds <id>` then `deploy <id> <build> --yes`.

**A public address answers 404 or 502.** The app may still be starting, or the
port in its settings doesn't match what the program listens on.

**An app can't reach another resource.** Resources are closed to each other
unless allowed. `reach ID` on the one it calls shows who may; ask the user
before opening it. A database is reached only by what links it: with the
user's agreement, `link APP DB --yes`.

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
