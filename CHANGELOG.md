# Changelog

Releasing: bump `metadata.version` in `.claude-plugin/marketplace.json` and in
every changed skill's `SKILL.md` together, add an entry below, then tag
`vX.Y.Z`. CI checks the skills and attaches a zip of each one to the release.
Claude Code users get the update only when the version changes.

## 1.12.0

Needs LiveLLM with inside access (api 0.51); against an earlier one
everything still reaches everything and `reach` shows no setting.

- Inside the workspace (`references/inside-access.md`): resources don't reach
  each other unless the user allows it. Every resource is closed, ones made
  earlier too; its `reachableFrom` names who may reach it (`[]` nothing, ids,
  `["*"]` the whole workspace). A Composable App is one resource with one
  setting: its services reach each other. Links, `dependsOn` and a Browser
  API's browsers are reached without it.
- A database has no `reachableFrom`: it is reached only by what links it.
  `create` and `set` refuse one on a database before sending (`[]` and `null`
  pass, the API drops them), `reach DB` shows what links it and refuses every
  change, and the API's 422 points at `link`.
- `llc.py link ID DB... [--remove] --yes`: an app, a machine or a Desktop App
  links databases with no variables, which only lets it reach them (nothing
  restarts; links with variables already there are kept). `--remove` takes
  links out, one with variables too (its variables leave and the app
  restarts, as with the CLI), and says when the resource still reaches the
  database through its `dependsOn` or another service of its Composable App.
  An app's link may name no variables (`{"id": "cache"}`); a machine's or a
  Desktop App's link never does (`"databases": [{"id": "db"}]` in its create
  file, `env` refused before sending by `create` and `set`). `ls` shows their
  links, and `reach` lists them as `links it`.
- `llc.py reach ID` shows who reaches a resource and its inside addresses,
  read from the workspace's settings only (it never connects, so it holds no
  machine and makes no link or token); a Composable App's name works too.
  `--from a,b`, `--from '*'`, `--none`, `--add`, `--remove` change it (with
  `--yes`, after the user agreed); `--add` and `--remove` are refused on a
  resource with no setting yet. `browser-api add` needs `--yes` now: putting a
  browser in a Browser API lets in whatever reaches it. `create` and `set` keep `reachableFrom`
  as written and refuse a malformed one before sending; `ls` shows it.
- The Network permission: an agent or a key needs it to let one resource
  reach another, unless it made both or the one reached already lets the
  whole workspace in with `["*"]` (never a database). Linking a database it
  didn't make and adding a service to a Composable App it didn't make need it
  too. A 403 `network_permission` says: ask the user; a person turns on
  Network on the Agents page or the Keys page.
- `SKILL.md`: new rule 11 says, word for word, to ask the user and wait for
  their agreement before letting a resource reach another (a Composable App is
  one resource, a database is reached only by what links it, and a service
  added to a Composable App counts); rule 10's two sentences are 1.11.1's.
  `tools/validate.py` fails when any of the three sentences is missing. A
  "Pick the tool" row for `reach`, and a 403 row for Network.
- `apps.md`, `databases.md`, `browsers.md`, `browser-api.md` and
  `troubleshooting.md`: an internal port and a database's private address
  answer only the resources allowed; joining an app takes its setting (an
  agent joins only apps it created); the Browser API asks no key inside, so
  putting a browser in one is an opening; a restored database is reached by
  nothing until it is linked; adding a service to a Composable App is an
  opening. `machines.md`: machines that talk to each other each need a
  `reachableFrom`, and a machine or a Desktop App links the database it uses. A public address given so that another resource
  can reach it counts as letting it in (rule 11).

## 1.11.1

- Changing a browser's proxies and exporting, importing or copying its
  profile need no permission of their own any more: the Proxies and Profiles
  permissions are gone from the Keys and Agents pages. An agent still needs
  what any change to the browser needs (Create on a browser it made, or Manage
  everything); an export needs Connect, and a copy needs Connect on the
  browser it copies from.
- What stands in their place is the user's agreement, every time. Rule 10 now
  says it word for word: "Before you change a browser's proxies (set, clear or
  rotate), ask the user and wait for their agreement: it changes where the
  browser's traffic goes and the address sites see." and "A profile holds the
  user's sign-ins. Before you export, import or copy one, or add cookies, ask
  the user and wait for their agreement. Never upload an exported file."
  `tools/validate.py` fails when `SKILL.md` lacks either sentence.
- `proxies.md` ("Ask first"), `profiles.md` (the permission table is gone),
  `browsers.md` and `workspace.md` say the same. `llc.py` no longer points a
  403 at a Proxies or Profiles permission. The `--yes` refusals and help of
  `proxy`, `profile` and `cookies` say to ask the user and wait for their
  agreement, and so do the cookie hints in `browsers.md` and
  `troubleshooting.md`. A plain 403 names the Keys page for an API key, beside
  the Agents page for an agent.
- A workspace that keeps profiles to its own people also refuses an import or a
  copy by an agent or an API key that isn't the workspace owner's, not only an
  export: `profiles.md` and `llc.py`'s hint for that 403 say so.
- Needs LiveLLM API 0.50.0 or newer, which drops the two permissions. An older
  one still refuses without them, and its 403 then gets the generic hint: tag
  this release only once that API is live.

## 1.11.0

- Camoufox browsers: a second browser engine, Firefox-based, beside Chrome
  (the default). Pick it when a site blocks Chrome as a bot or says you use a
  VPN when you don't. `llc.py create browser --json FILE --engine camoufox
  --yes` (or `"engine": "camoufox"` in the file); `llc.py engines` lists the
  engines this LiveLLM offers. A browser's engine is set when it is made and
  can't change; `ls` names a Camoufox browser. A create that a LiveLLM
  without engines turned into Chrome is said, not passed off as Camoufox.
- One Browser API holds Chrome and Camoufox browsers together: `browser-api
  create` takes no engine, `--all` is every browser in the workspace, and
  remote browsers are Chrome. `POST /start_session` takes an optional
  `engine` (`{"engine": "camoufox"}`) to start on a browser of that engine;
  without it, the browser with the fewest open tabs over all of them.
- A Camoufox browser's `connect --tool cdp` answers `playwright` (address,
  header and version) instead of `cdp`. It takes **Playwright 1.62 only**;
  another version is refused with a 428. A later Camoufox release may move
  that pin, and the answer's `playwright.version` always names the one to
  use. New `assets/playwright_connect.py` and `.mjs` check the installed
  version first and print the exact `pip` or `npm` line; `connect --env`
  exports `LIVELLM_PLAYWRIGHT_URL` and `LIVELLM_PLAYWRIGHT_VERSION`.
- Earlier skill versions can't drive a Camoufox browser: their
  `cdp_connect` and `connect --env` look for `cdp` and find none. From this
  version, `cdp_connect.py` and `.mjs` given a Camoufox answer point at
  `playwright_connect` and exit 2; with Chrome they work as before.
- `browsers.md`, `browser-api.md`, `profiles.md`, `proxies.md` and
  `troubleshooting.md`: work in `contexts[0]`, never `browser.new_page()`; a
  context of your own needs `no_viewport=True`; `page.evaluate` runs apart
  from the page, `mw:` runs it in the page; no extensions (an ad blocker is
  built in); a Browser API holds both engines, and `start_session` takes
  `engine`; profiles move only between browsers of one engine, cookies
  between any (a cookie Camoufox refuses is counted in `dropped`); a new
  Firefox major version draws a new fingerprint; a context with its own proxy
  goes around the browser's proxies.
- `llc.py` answers the new refusals with what to do: 422
  `engine_unavailable`, `engine_fixed`, `extensions_unsupported`,
  `engine_mismatch` (a profile copied from a browser of the other engine),
  `profile_engine`, a `not_livellm_profile` that names an engine (a profile
  from the other engine: add its cookies), and a 409 `profile_newer` from a
  newer Camoufox. A database's engine refusal keeps its own answer. Chrome's
  answers from a LiveLLM without engines are unchanged.
- `llc.py engines` asks with the sign-in or key when there is one, and
  without one, or with one the list won't take, reads the public list.

## 1.10.1

- A profile import of a password-protected file sent without its password
  (422 `password_required`, "This file is password protected.") now gets the
  same answer as a wrong password: ask the user for the file's password, put it
  in an environment variable and pass `--password-env VAR`; never guess one,
  never put it on the command line. `profiles.md` names both refusals.

## 1.10.0

- A browser's language and time zone: `locale`, `timezone`, `languages` and
  `geolocation` in the create file or in `set` (`{"browser": {...}}`).
  Changing them restarts the browser; its profile is kept. `llc.py locales`
  lists the languages offered and the time zone names. `browsers.md` says how.
- Proxies (`references/proxies.md`): `llc.py proxy show|set|rotate|clear|remove`.
  HTTP, HTTPS and SOCKS5 proxies with logins, mobile proxies with a change-IP
  address (called with `GET` or `POST`) and its shortest interval, rotation
  by hand (`--yes`: it drops open connections), on a timer or per Browser API
  session. Logins are written once and never shown again; a login written
  into a proxy address is refused before anything is sent, in `proxy set`,
  `create` and `set` alike. It needs the Proxies permission, which only a
  person gives (Agents page, Keys page); what a proxy covers and what it
  doesn't is spelled out.
- Profiles (`references/profiles.md`): `llc.py profile show|snapshot|restore|rm`
  and `export|import|copy`; `llc.py cookies ID --json FILE --yes` adds cookies
  to a running browser. Export, import and copy need the Profiles permission
  (import and copy also Manage, or Create on a browser you made); an export's
  password comes from an environment variable, and the file is saved readable
  only by you, never over another file, a half-saved one included. An import
  refused before the whole file went out says why, instead of a network
  error. A browser made earlier asks for one restart first.
- `SKILL.md`: rule 10 (profiles hold sign-ins; proxy logins in a file you
  delete; never work around the two permissions), three rows in the table,
  and the `browser_proxy` and `browser_profile` tools. `llc.py` answers each
  new refusal (403, 409, 413, 422, 429, 507) with what to do, from its code or
  its message; a workspace that keeps exports to its own people is told
  apart from a missing permission.

## 1.9.0

- Where it runs: any resource may carry `placement`, a region
  (`{"strategy": "region", "region": "<r>"}`) or a pinned host
  (`{"strategy": "host", "host": "<id>"}`); left out, LiveLLM picks the host.
  `machines.md`, `apps.md`, `databases.md`, `browsers.md` and `browser-api.md`
  say how to set it, change it (it restarts the resource there) and go back to
  automatic with `null`; one pinned to a host waits while that host is down.
- `llc.py hosts` lists where resources can run: each host's id, region, zone,
  host group, free processor and memory, GPUs, and whether it is ready and
  takes new resources (`schedulable`); a location is accepted only on a host
  that is both.
- `llc.py restore` (a database) and `llc.py browser-api create` take `--host`
  or `--region`. A restored database is automatic unless you say otherwise;
  it never takes the original's location. A flag given with no value is
  refused, never taken to mean automatic.
- `llc.py template use` takes `--host`, `--region` or `--automatic` (or
  `placement` in `--json`): a template keeps where its resources ran unless
  you say otherwise. `workspace.md` says how.
- A database still on its first start refuses a new location (409): `llc.py`
  says to ask the user before deleting and creating it again, not to retry.
- `troubleshooting.md`: a resource with a location that waits for room, or
  whose host is gone, and what to offer the user.

## 1.8.0

- `workspace.md`: the owner is emailed when an alert opens and when it is
  over (the switch is theirs, on the Monitoring page); how to read open and
  resolved alerts; a machine's memory is its own system's figure.
- `llc.py exec` gives a command 300 seconds by default (was 60), as the
  platform now does; `--timeout` still takes up to 600.
- `llc.py create apps --json FILE --join APP` (or `"join"` in the file) adds
  services to an app that is already there, in one step: they take its stack,
  and an app on its own gets a stack named after itself (it restarts once).
  `apps.md` says how.

## 1.7.0

- `llc.py template use --secret portPasswords.<port>.<user>=…` keeps a
  username with dots (`alice.smith`, `a@b.com`) whole. `databases.md` quotes
  the URL refusal as it reads now.
- `workspace.md` and the README: the workspace's SSH keys reach every
  machine, Linux and Windows, and jobs run on both.
- `machines.md`: `unshare` closes the links you made; another agent's or the
  user's is refused (403), unless you may change anything.
- A Desktop App is one desktop (breaking): `machines.md` makes one for each
  desktop needed; `llc.py connect`, `exec` and `share` no longer take
  `--desktop`, and a Desktop App's settings have no `replicas` (more than 1
  is refused). `SKILL.md`'s table and connector notes follow.
- `machines.md`: Windows 11 gets SSH (and `exec`) from Windows Update, so a
  workspace without internet access never gets it there; use Windows Server,
  the screen or Remote Desktop. A new Windows 11 is retried for up to 15
  minutes, not open-ended.
- `SKILL.md` names the connector's screen tools for handing over:
  `share_screen` (mode control) and `stop_sharing` for a machine or a Desktop
  App, `connect_resource` with tool view for a browser's live view.
- Apps and their databases: `llc.py create apps --json FILE` takes
  `{"apps": [...], "databases": [...]}` and makes all of it in one step, a
  database's password left to the platform; each app links its databases in
  `databases: [{id, env: {VAR: detail}}]`. The answer lists the `databases`
  made. `ls` shows an app's links as its settings hold them, a database's
  `usedBy` and its login's `username`. `rm ID --with-databases` deletes an app
  with the databases made with it that no other app uses (and says which went
  and which stayed); `--force` deletes what another app's settings name. `rm`
  waits up to three minutes for the answer, and when it is lost on the way it
  looks at the workspace: gone is deleted.
- Templates: `template save NAME --from ID [--description D]` has LiveLLM read
  the resource: an app keeps its plain env values and its secrets' names; an
  app of a Composable App saves the whole app, its databases and links.
  `template use T NEW` creates through the template (a Composable App with its
  databases, already linked, under the name NEW) and takes the secrets it left
  out from `--secret-env PATH=VAR`, `--secret PATH=VALUE` or `--json FILE`
  (`secretEnv`, `imagePassword`, `gitToken`, `portPasswords`, `credentials`,
  `services`; a file with other settings is refused). A refusal for missing
  secrets names them, and `next` spells the flags.
- Older versions: 1.6.6 and before build the resource from a template's
  settings themselves. A template saved now keeps its secrets as names only
  (and a Composable App's as kind `stack`), so `template use` there is
  refused (`value required`, or an unknown kind). Update to use one;
  templates saved before still work there.
- `references/workspace.md`: an app's template makes one app on its own,
  never one in a Composable App.
- `references/databases.md`: "Link it to an app" (what PostgreSQL and Redis
  give, the password and URL never shown, the app waiting for them, a linked
  database refused a delete, linking on an existing app, the 422 for a URL
  from a password set before links); a platform-made password; changing a
  password reaches linked apps on restart. `references/apps.md`: "An app with
  its databases", a Nextcloud with PostgreSQL and Redis in one step.
  `references/workspace.md`: templates of a whole Composable App, and `use`
  with its secrets. `SKILL.md`: the app-with-a-database example makes both in
  one step.
- `tests/livellm-cloud/test_llc_databases.py`: apps with databases, links in
  `ls`, deletes, template save and use against a stand-in for the API.
- `llc.py exec` waits for a long command: a command still going when the call
  answers keeps going on the machine, and `exec` looks at it again until it
  ends, up to `--timeout` (then the platform stops it, exit code 124) and two
  minutes more. The answer is `done`, `exitCode`, `output` (stdout and stderr
  together), `truncated`, `durationMs` and `runId`. `SKILL.md` and
  `references/machines.md` say so; `SKILL.md` names the connector's
  `command_output` for a command `run_command` left running.
- `tests/livellm-cloud/test_llc_exec.py`: exec against a stand-in for the API.
- Handing over: rule 6 says to give the user a link they use themselves — a
  browser's live view, or a control screen link (`share ID --control`) for a
  machine or a Desktop App — then wait, and never to send them to the
  console. `connect ID --tool view` on a machine or a Desktop App now returns
  `liveView.url`, a page to open in any browser for 15 minutes, and
  `references/machines.md` says so.
- `references/apps.md`: an HTTP port's `proxy.trust` is the range the HTTPS
  proxy connects from; the app should trust it for `X-Forwarded-For` and
  `X-Forwarded-Proto` (with the Nextcloud, Django and Express settings).
- Windows machines take commands and SSH: `exec` (and `run_command`) runs in
  PowerShell on Windows 11 and Windows Server, bash elsewhere; a Windows
  machine answers SSH with PowerShell for its login and takes the workspace's
  keys and its own `credentials.sshKeys`. `references/machines.md` says so,
  with what to do when a command fails with "is not recognized" and when a
  new Windows 11 machine's SSH isn't up yet; the `exec` help says which shell
  runs where.
- `llc.py login` signs in in two calls: the first prints the link as JSON
  (`signedIn: false`, `link`, `code`, `expiresAt`) and returns at once; once
  the user has pressed Allow, `login` again finishes the same sign-in, waiting
  up to a minute and then saying it is still waiting (exit 3). A link that ran
  out or was denied is replaced by a new one. The started sign-in is kept in
  `credentials.pending.json` next to the credentials. `login --wait` is the
  one-call form, for a person at a terminal. `SKILL.md` says to run login, give
  the user the link, and run login again after they allow it. Without
  `--access`, login again finishes the pending sign-in with the access it
  asked for (also one the CLI started); only a different `--access` starts
  over.
- `SKILL.md`: `run_command` and `list_machines` are on the one connector; the
  terminal connector is gone.
- `tests/livellm-cloud/test_llc_login.py`: the sign-in, against a stand-in
  server (`python3 -m unittest discover -s tests/livellm-cloud`).

## 1.6.6

- `references/apps.md`: an app's disks are `volumes` only; the single
  `"storage": {"size", "mountPath"}` form is no longer taken.

## 1.6.5

- `llc.py build ID --wait`: follows the build it started until the app is
  live, or prints why it failed with the end of its log (exit 3 when it is
  still going after `--timeout`). The app example uses it to build again
  after a failed build.
- `llc.py monitoring [ID] [--range …]`: every resource up or down, its uptime,
  use and alerts; with a machine's id, that machine in detail.
  `references/workspace.md` reads it instead of pointing at the console.
- `llc.py activity [--actor you|platform|all] [--object ID] [--limit N]
  [--before EVENT]`: what happened in the workspace and who did it.
- `llc.py templates`, `template show T`, `template save NAME --from ID` and
  `template use T NEW [--json FILE] --yes`, `template rm T --yes`: a
  resource's settings kept for making more like it, without logins, env
  values or pull credentials. A Browser API saves as one too, as the console
  saves it; a database's leaves out what it was restored from.
- `references/browser-api.md`: a 503 from a Browser API just made means its
  browsers are still joining: wait until `GET <url>/browsers` lists them (up
  to about 2 minutes), then retry.

## 1.6.4

- Databases: backups are `daily` (a full copy each night), `continuous` (the
  nightly copy plus every change, restore to any minute) or `manual` (only
  when asked), kept `keepDays` days. `references/databases.md` no longer says
  restoring is done in the console: `llc.py restore db BACKUP --as NEW --yes`
  restores into a new database, `--at TIME` to a minute; the new database
  keeps the login name and takes a new password from `--password-env VAR`. It also gives the
  real public address (`<id>-<workspace>.cloud.live-llm.com`, Postgres 5432,
  Redis 6380, TLS only), says Redis keeps its keys across restarts (and has
  no backups), that three instances are Postgres only, and that a database
  can be restarted.
- Machines: `references/machines.md` covers backups: take one now (live, or
  `--clean` with the machine stopped), restore in place with the machine
  stopped, and a schedule that keeps the last N.
- `llc.py backups ID`, `backup ID [--clean] [--name N]` and `restore ID BACKUP
  [--as NEW --password-env VAR] [--at TIME] --yes`. Restore refuses a database without `--as`
  and a machine with it before sending anything.

## 1.6.3

- Browser API: one address in front of several browsers.
  `references/browser-api.md` covers which browser answers (the one with the
  fewest open tabs, the session's, or the one named by `/browsers/<name>/…`
  or `X-Browser-Id`), sessions, a workspace key for clients that run longer
  than a token, and the errors. The routing table points at it.
- `llc.py browser-api create NAME --browsers a,b --yes` (or `--all`, and
  `--remote ID=wss://…`), `browser-api show NAME`, and `browser-api add
  NAME BROWSER` / `remove NAME BROWSER --yes` to change its browsers one at a
  time (taking one out ends its sessions, so `remove` needs the user's yes). A
  remote browser with a login header goes through `create controller --json`.
- `llc.py set ID --json CHANGES --yes`: change only the settings the file
  holds, once the user agreed; a list replaces the whole list and `null`
  removes a setting.
  `stop` and `start` change only whether the resource runs, so a change made
  meanwhile in the console is kept.

## 1.6.2

- Apps: raw TCP and UDP ports (`"tcp": true` / `"udp": true`) with a public
  `host:port`, limited by `allowCIDRs`; several `volumes` per app (grow only,
  removing one deletes its data; `storage` is the one volume `data`).
- `stop ID --yes` and `start ID`: stop an app or a machine without deleting
  it; its disks are kept and only they are billed. Browsers and databases
  can't be stopped, and `stop` refuses them.

## 1.6.1

- The routing table names every kind of resource: Linux servers (Ubuntu,
  Debian, Fedora), the Ubuntu desktop, Windows 11, Windows Server Core,
  Desktop Apps, Composable Apps, browsers, PostgreSQL and Redis, screen links
  and running commands.
- The references use the console's names (Composable App, Desktop App, a
  Browser under Apps) and say that `exec` and workspace SSH keys work on every
  Linux machine.

## 1.6.0

- Machines: Debian 13 and Fedora 44 servers (`"os"`), and Windows Server is
  now Server Core.
- `exec`: run a command on a Linux machine or a Desktop App and get its output
  and exit code, no SSH needed. It needs the Run commands permission.
- `share`, `shares`, `unshare`: a link that lets the user watch a screen or
  take it over, instead of sending them to the console.
- `release`: let a machine go when you're done, so other agents can use it.
- `connect --desktop N` for Desktop Apps, and `--screen-width` / `--format`
  for smaller screenshots.
- A 409 that names another agent means the machine is held; the skill says to
  wait or use another.

## 1.5.2

- The README points at the command line's own repository, and at the one-line
  prompt that sets an agent up from docs.live-llm.com/SKILL.md.
- The release is the latest again, so the Claude.ai zip downloads from
  releases/latest.

## livellm 0.1.1 (the command line)

- `ls` says why it couldn't read how things are running, instead of showing
  "unknown" for everything without a word.
- The README says why `login` asks for full access by default.

## livellm 0.1.0 (the command line)

- `livellm`: sign in, list, status, logs, connect, keys, create, rm, restart,
  build, builds, deploy. One binary, no dependencies, five platforms.

## The command line moved

`livellm` now lives at [qalby-tech/livellm_cloud_cli](https://github.com/qalby-tech/livellm_cloud_cli)
(from its 0.1.3). The `cli-v*` tags here stay as they were.

## 1.5.1

- `create apps --json stack.json`: several apps at once, all or nothing.

## livellm 0.1.2 (the command line)

- `create apps -f stack.json`: several apps at once, all or nothing.

## 1.5.0

- The `compose` command is gone for now, with the platform's compose file
  import. Several apps are still created together with `POST /v1/workloads`.

## 1.4.0

- Apps that work together: a `stack` whose apps reach each other by plain name
  (`db:5432`), `internal` ports with no public address, and `dependsOn` for what
  an app needs first.
- `compose <file>`: a compose file becomes a stack. It plans first — the apps,
  notes on what was chosen or left out, the variables it needs — and creates
  every app at once with `--yes`, all or nothing.

## 1.3.0

- Desktops can be worked on, not only watched: `connect <id> --tool computer`
  gives an address and the seventeen actions a computer-use tool already knows
  — screenshot, click, type, drag, scroll and the rest. Checked against a
  running KDE desktop.

## 1.2.0

- `logs <id>`: the last log lines of a resource with each container's state,
  restarts and resource use — what to read when something runs but misbehaves.
- Never ask the user to send a login code: send them the live view link so they
  type it themselves. Two independent judges scored the hand-over against a
  version of this task without the skill; both preferred the skill's answer, and
  both named the same failure without it — tearing down the user's own browser.

## 1.1.2

- Says that stopping a machine clears its stop time.

## 1.1.1

- Says how long a key change takes to reach a running machine: a minute or two,
  the same both ways. Checked against a running machine, adding and removing.

## 1.1.0

- SSH keys: a machine can be created with its own public keys
  (`credentials.sshKeys`), and `ssh-keys` reads the workspace keys its owner
  set. Both sets are installed together on every Ubuntu machine, including ones
  already running; neither removes the other. A machine that booted with no
  keys at all takes its first one after a restart.
- A machine can be given a stop time (`stopAfter`), so one made for a single
  job doesn't run forever. `connect` shows when it stops; stopping keeps the
  disk.

## 1.0.0

- Checked end to end against a running LiveLLM: an agent signs in with the
  approval link, creates an app, a database, a browser and a machine, reaches
  each of them, and deletes what it created. The app's address refuses a
  request without its password, a real Playwright client drives the browser and
  is refused without a token, and the machine answers on its SSH port.

## 0.2.0

- First release you can install.
- `livellm-cloud`: sign-in with a one-click approval link, then browsers,
  machines, desktops, apps and databases, with a reference per tool and
  Playwright examples.
- Checked against the running platform: signing in, listing resources and
  connecting to a browser and a machine. Creating each kind of resource is
  described from the API's own definitions but not yet run end to end, which
  is why this is 0.2 and not 1.0.

## 0.1.0

- Repository layout, validation and release packaging.
