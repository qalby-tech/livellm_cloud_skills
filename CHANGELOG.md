# Changelog

Releasing: bump `metadata.version` in `.claude-plugin/marketplace.json` and in
every changed skill's `SKILL.md` together, add an entry below, then tag
`vX.Y.Z`. CI checks the skills and attaches a zip of each one to the release.
Claude Code users get the update only when the version changes.

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
