---
name: livellm-cloud
description: Gives an agent real computers on LiveLLM Cloud. Drive a Chrome browser over CDP, or a Firefox-based Camoufox browser with Playwright for sites that block Chrome, while a person watches the live view, give it a language, a time zone and the user's own proxies (rotating, mobile ones too), save, restore, export or import its profile, put several browsers behind one Browser API address, run commands on Linux (Ubuntu, Debian or Fedora) and Windows machines, work on Ubuntu or Windows desktops and Desktop Apps by screenshot and click, share a screen with the user by link, deploy apps from a Docker image or a Git repo, and create Postgres or Redis databases and S3 object storage. Use when the user asks to automate or log into a website with a real browser, get a server or a desktop, run code on another machine, deploy an app, spin up a database or S3 bucket storage, or check what is running in LiveLLM. Do NOT use for LiteLLM, local Docker, or other cloud providers.
license: MIT
compatibility: Needs outbound HTTPS to the LiveLLM Cloud API and Python 3.9 or newer. Signs in through a one-click approval link, or uses LIVELLM_API_KEY for unattended runs.
metadata:
  author: LiveLLM
  version: 1.13.0
  documentation: https://docs.live-llm.com
---

# LiveLLM Cloud

Real computers the user owns: browsers, machines, desktops, apps, databases
and object storage.
Everything goes through `scripts/llc.py`, which talks to the API and prints JSON.

## Rules

1. Never print, log, commit or paste the sign-in file, its tokens, an API key
   or a private SSH key. Never put them on a machine, a web page, or into an
   app's settings.
2. Create, resize and delete only what the user asked for. If you decide you
   need something extra, ask first and say what it uses against their plan.
3. Delete only resources you created. Never delete anything to get under the
   plan limit.
4. When the plan is full (a 402), stop and show usage. The user decides.
5. Never open a port to the internet without a password or an allowed-address
   list, unless the user asked for a public site.
6. Hand login codes, CAPTCHAs, passwords, payments and confirmations to the
   user through a link they use themselves: a browser's live view
   (`connect ID --tool view`), or a control screen link for a machine or a
   Desktop App (`share ID --control`). Then wait until they say they are done.
   Never try to solve or get around them, never ask the user to send you a
   code, and never tell them to find the screen in the console.
7. Generate strong passwords for databases and ports (an object storage's
   secret key too), pass them to the app that needs them, and show the user
   once. They can't be read back later. A database made with its app and
   linked to it needs none from you. For a machine you create, log in with
   an SSH key of your own rather than the password, and give it a stop time
   when the work has an end.
8. Use only `scripts/llc.py`, plain SSH, and a browser library such as
   Playwright. Nothing else needs to run.
9. When you are done with a machine you ran commands on or worked the screen
   of, `release` it so other agents can use it.
10. A profile holds the user's sign-ins. Before you export, import or copy
    one, or add cookies, ask the user and wait for their agreement. Never
    upload an exported file. Before you change a browser's proxies (set,
    clear or rotate), ask the user and wait for their agreement: it changes
    where the browser's traffic goes and the address sites see. `proxy
    remove`, and a create or set carrying proxy settings, count too. Proxy
    logins and change-IP addresses go in a file you delete after sending.
11. Resources in a workspace can't reach each other unless the user allows
    it. A Composable App counts as one resource, and a database is reached
    only by what links it. Before you let a resource reach another
    (reachableFrom, a database link, dependsOn, a service added to a
    Composable App, or a browser put in a Browser API), ask the user and wait
    for their agreement, unless you created both or the one reached already
    lets the whole workspace in.
    Letting the whole workspace in always needs their agreement. An API key
    or an agent also needs the Network permission for this, which only a
    person turns on. Giving a resource a public address so that another
    resource here can reach it is letting it in too. Never get around a
    refusal, for example with a public address
    (`references/inside-access.md`).

## Signing in

Run `python3 scripts/llc.py whoami`. If it says "not signed in":

```
python3 scripts/llc.py login
```

It prints one link with a code and returns. Give the user the link and ask them
to click Allow. Once they say they have, run `login` again: it finishes the same
sign-in and saves it for next time. If it says it is still waiting (exit 3), ask
the user whether they pressed Allow, then run it once more; a link that ran out
or was denied is replaced by a new one to give them.

It asks for "create" access: use everything, and manage what this agent
creates. Running commands is a separate permission ("Run commands") the user turns on
for this agent on the console's Agents page; "full" access includes it.
Letting resources reach each other inside the workspace takes Network, which
no access level includes: only a person turns it on (rule 11). For
runs with nobody present the user can set `LIVELLM_API_KEY` instead, and
`LIVELLM_API_URL` points at a self-hosted LiveLLM.

## Pick the tool

| The user wants | Resource (`create` type) | Read |
|---|---|---|
| A server: code built or tested, a job, a service, SSH | Linux server: Ubuntu, Debian or Fedora (`vm-ubuntu`, `"os"`) | `references/machines.md` |
| Commands run on a machine, output back, no SSH | `exec` on a machine (PowerShell on Windows) or a Desktop App | `references/machines.md` |
| A Linux desktop a person looks at or you click through | Ubuntu desktop (`vm-ubuntu-desktop`) | `references/machines.md` |
| Windows software, a Windows desktop | Windows 11 (`vm-windows`) | `references/machines.md` |
| A Windows server, no desktop | Windows Server Core (`vm-windows`, `"windowsEdition": "server"`) | `references/machines.md` |
| A Linux desktop that starts in seconds, one for each task or agent | Desktop App (`desktop`) | `references/machines.md` |
| The user to watch a screen, or take it over | Screen link (`share`) | `references/machines.md` |
| A service or site online, from an image or a Git repo, one service or several; a raw TCP/UDP port (game server, VPN) | Composable App (`pod`) | `references/apps.md` |
| A site automated, logged into, scraped or tested in real Chrome | Browser (`browser`) | `references/browsers.md` |
| A site that blocks Chrome as a bot or says "VPN" when there is none | Camoufox browser (`create browser --engine camoufox`) | `references/browsers.md` |
| A browser in another language or time zone | Browser `locale`, `timezone` | `references/browsers.md` |
| A browser through the user's proxies; rotating IPs, mobile proxies | `proxy set`, `proxy rotate` | `references/proxies.md` |
| A browser's sign-ins kept as a snapshot, switched back, exported, imported, moved; cookies added | `profile`, `cookies` | `references/profiles.md` |
| Many pages fetched or scraped at once through one address; several browsers behind one API | Browser API (`browser-api create`) | `references/browser-api.md` |
| One resource talks to another inside the workspace: a machine to a Browser API; an app, a machine or a Desktop App to a database | `reach`; a database: `link` | `references/inside-access.md` |
| A database | PostgreSQL (`storage`, `"engine": "postgres"`) | `references/databases.md` |
| A cache or a queue | Redis (`storage`, `"engine": "redis"`) | `references/databases.md` |
| Files, uploads, buckets; S3 storage for an app | Object storage (`storage`, `"engine": "s3"`) | `references/databases.md` |
| Cost, limits, what is running, alerts, what happened, templates, signed-in agents, held machines | Workspace | `references/workspace.md` |

## The loop

1. **Check the sign-in.** `whoami`. If signed out, `login`, give the user the
   link, and run `login` again once they click Allow. Nothing else works until
   they do.
2. **Look before creating.** `ls` shows every resource, its state and who made
   it. Reuse what the user already has: a browser already logged in to a site is
   worth more than a fresh one.
3. **Create only what was asked for.** `create <type> --json body.json --yes`.
   Leave `placement` out unless the user wants a region or a host (`hosts`
   lists them). A new resource is closed to the rest of the workspace: what
   should reach it goes in `reachableFrom`, and a database is reached by what
   links it (rule 11).
4. **Wait.** `wait <id>` until it is ready. On a timeout, tell the user what the
   status said. Never guess.
5. **Connect.** `connect <id>` prints the address and a token that opens it for
   15 minutes. Pass them straight to your client; reconnecting asks again.
6. **Work, and hand over when a person is needed.** Send the live view link
   (a browser) or a screen link (a machine or desktop) for login codes,
   payments, and anything you should not decide alone.
7. **Finish.** Say what exists now and give the links. Stop or delete only what
   you created, and say so before you do. `stop <id> --yes` keeps an app's or
   a machine's disks and bills only them; `start <id>` runs it again.
   `set <id> --json changes.json --yes` changes only the settings in the file,
   and only after the user agreed: a list in it replaces the whole list and
   `null` removes a setting, so leaving out a volume deletes its data.

## Examples

### Log into a site and take something out of it

```
python3 scripts/llc.py ls --type browser
python3 scripts/llc.py connect shop --tool cdp
```

Connect a Playwright client to `cdp.url` with the header from `cdp.headers`
(see `assets/cdp_connect.py`; a Camoufox browser answers `playwright` instead:
`assets/playwright_connect.py`), drive the page, and when the site asks for a
code, run `connect shop --tool view` and give the user that link so they can
type it while you wait. Leave the browser running: it stays logged in for
next time.

### Run a job on a clean machine

```
python3 scripts/llc.py create vm-ubuntu --json machine.json --yes
python3 scripts/llc.py wait ci-box
python3 scripts/llc.py exec ci-box "git clone https://github.com/you/app && cd app && make test" --session job --timeout 600
python3 scripts/llc.py release ci-box
```

`machine.json` carries the id, the size and the login to create
(`references/machines.md`). `exec` waits for the command to end (up to
`--timeout`) and answers with its exit code and output; SSH works too (`connect` prints the address). Release the machine when the job
is done, and delete it when the user is done with it: you made it.

### Ship an app with a database

```
python3 scripts/llc.py create apps --json shop.json --yes
python3 scripts/llc.py progress web
```

`shop.json` holds `{"apps": [...], "databases": [...]}`: the app, and the
database made with it in the same step. The app links it
(`"databases": [{"id": "shop-db", "env": {"DATABASE_URL": "url"}}]`), so the
platform makes the password and hands it to the app; no password passes
through you (`references/apps.md`, "An app with its databases"). The link
lets the app reach the database, and you made both, so it needs nothing more
(rule 11). Creating the
app starts its first build; `progress web` shows how it is going. When a build fails, read `progress`, fix the
repository, then `build web --wait`: it builds again and waits until the app
is live, or prints why not. To go back to what worked: `builds web`, then
`deploy web <build> --yes`.

Machines and Postgres databases have backups (object storage keeps one copy
and has none): `backups <id>` lists them, `backup <id>` takes one now. A database restores into a new one
(`restore db <backup> --as db-restored --password-env VAR --yes`); a machine is put back in place
and must be stopped first. Restore only when the user asked for it.

## When something is refused

Every error prints `{"error": ..., "next": ...}`. Do what `next` says.

| Answer | What it means | What to do |
|---|---|---|
| not signed in, 401 | No sign-in, or it ended | `login`, give the user the link, `login` again after Allow |
| 402 | The plan is full | Stop, show usage, let the user choose |
| 403 | Beyond this agent's permissions, or someone else's resource | Tell the user which permission it needs; they turn it on on the Agents page |
| 403 naming Network | Letting one resource reach another | Ask the user (rule 11); only a person turns on Network, for an agent on the Agents page, for a key on the Keys page |
| 404 | No such resource here | `ls`; the id is probably wrong |
| 409 | Another agent holds the machine, or the resource is mid-change | Held: wait until the time it names, or use another. A database "still starting" can't move: ask the user, don't retry. Otherwise wait a few seconds, retry once |
| 422 | A value was refused; the message names it | Fix that value, never retry unchanged |
| 5xx | Platform trouble | Retry twice with a pause, then tell the user |

More in `references/troubleshooting.md`.

## If LiveLLM tools are connected

When the agent already has LiveLLM tools of its own, use them instead of this
script. The steps and the rules above stay the same. The `computer` tool works
a desktop, `run_command` runs a shell command (never type commands into a
screen; one still going after its wait answers `done: false` and a run id, and
`command_output` waits for the rest), `list_machines` shows where commands can
run, and `release_machine` lets a machine go. To hand the user a screen (rule
6), `share_screen` with mode control gives a link to a machine or a Desktop
App and `stop_sharing` closes it; for a browser, `connect_resource`
with tool view gives its live view; with tool cdp, a Camoufox browser
answers a Playwright address (follow its `how`). `browser_proxy` reads a
browser's proxies and sets, clears or rotates them (ask first, rule 10), and
`browser_profile` lists, takes, restores and deletes its profile snapshots and
copies another browser's profile into it (ask first, rule 10); exporting or
importing a profile file stays with this script. `create_resource` and
`update_resource` take `reachableFrom` and database links (rule 11; a database
takes no `reachableFrom`), and `connect_resource` answers the inside addresses
under `inside`. A tool you lack permission for is not listed at all.
