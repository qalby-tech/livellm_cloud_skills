---
name: livellm-cloud
description: Gives an agent real computers on LiveLLM Cloud. Drive a Chrome browser over CDP while a person watches the live view, run commands on Linux machines (Ubuntu, Debian or Fedora), work on Ubuntu or Windows desktops and Desktop Apps by screenshot and click, share a screen with the user by link, deploy apps from a Docker image or a Git repo, and create Postgres or Redis databases. Use when the user asks to automate or log into a website with a real browser, get a server or a desktop, run code on another machine, deploy an app, spin up a database, or check what is running in LiveLLM. Do NOT use for LiteLLM, local Docker, or other cloud providers.
license: MIT
compatibility: Needs outbound HTTPS to the LiveLLM Cloud API and Python 3.9 or newer. Signs in through a one-click approval link, or uses LIVELLM_API_KEY for unattended runs.
metadata:
  author: LiveLLM
  version: 1.6.1
  documentation: https://docs.live-llm.com
---

# LiveLLM Cloud

Real computers the user owns: browsers, machines, desktops, apps and databases.
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
6. Hand login codes, CAPTCHAs, payments and confirmations to the user through
   the live view link. Never try to solve or get around them, and never ask the
   user to send you a code: send the link instead, so they type it themselves.
7. Generate strong passwords for databases and ports, pass them to the app that
   needs them, and show the user once. They can't be read back later. For a
   machine you create, log in with an SSH key of your own rather than the
   password, and give it a stop time when the work has an end.
8. Use only `scripts/llc.py`, plain SSH, and a browser library such as
   Playwright. Nothing else needs to run.
9. When you are done with a machine you ran commands on or worked the screen
   of, `release` it so other agents can use it.

## Signing in

Run `python3 scripts/llc.py whoami`. If it says "not signed in":

```
python3 scripts/llc.py login
```

It prints one link with a code. Give the user the link, ask them to click Allow,
and wait for the command to finish. The sign-in is saved for next time. It asks
for "create" access: use everything, and manage what this agent creates.
Running commands is a separate permission ("Run commands") the user turns on
for this agent on the console's Agents page; "full" access includes it. For
runs with nobody present the user can set `LIVELLM_API_KEY` instead, and
`LIVELLM_API_URL` points at a self-hosted LiveLLM.

## Pick the tool

| The user wants | Resource (`create` type) | Read |
|---|---|---|
| A server: code built or tested, a job, a service, SSH | Linux server: Ubuntu, Debian or Fedora (`vm-ubuntu`, `"os"`) | `references/machines.md` |
| Commands run on a machine, output back, no SSH | `exec` on a Linux machine or Desktop App | `references/machines.md` |
| A Linux desktop a person looks at or you click through | Ubuntu desktop (`vm-ubuntu-desktop`) | `references/machines.md` |
| Windows software, a Windows desktop | Windows 11 (`vm-windows`) | `references/machines.md` |
| A Windows server, no desktop | Windows Server Core (`vm-windows`, `"windowsEdition": "server"`) | `references/machines.md` |
| Several Linux desktops that start in seconds, one per task or agent | Desktop App (`desktop`) | `references/machines.md` |
| The user to watch a screen, or take it over | Screen link (`share`) | `references/machines.md` |
| A service or site online, from an image or a Git repo, one service or several | Composable App (`pod`) | `references/apps.md` |
| A site automated, logged into, scraped or tested in real Chrome | Browser (`browser`) | `references/browsers.md` |
| A database | PostgreSQL (`storage`, `"engine": "postgres"`) | `references/databases.md` |
| A cache or a queue | Redis (`storage`, `"engine": "redis"`) | `references/databases.md` |
| Cost, limits, what is running, alerts, signed-in agents, held machines | Workspace | `references/workspace.md` |

## The loop

1. **Check the sign-in.** `whoami`. If signed out, `login` and give the user the
   link. Nothing else works until they click Allow.
2. **Look before creating.** `ls` shows every resource, its state and who made
   it. Reuse what the user already has: a browser already logged in to a site is
   worth more than a fresh one.
3. **Create only what was asked for.** `create <type> --json body.json --yes`.
4. **Wait.** `wait <id>` until it is ready. On a timeout, tell the user what the
   status said. Never guess.
5. **Connect.** `connect <id>` prints the address and a token that opens it for
   15 minutes. Pass them straight to your client; reconnecting asks again.
6. **Work, and hand over when a person is needed.** Send the live view link
   (a browser) or a screen link (a machine or desktop) for login codes,
   payments, and anything you should not decide alone.
7. **Finish.** Say what exists now and give the links. Stop or delete only what
   you created, and say so before you do.

## Examples

### Log into a site and take something out of it

```
python3 scripts/llc.py ls --type browser
python3 scripts/llc.py connect shop --tool cdp
```

Connect a Playwright client to `cdp.url` with the header from `cdp.headers`
(see `assets/cdp_connect.py`), drive the page, and when the site asks for a
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
(`references/machines.md`). `exec` answers with the exit code and the output;
SSH works too (`connect` prints the address). Release the machine when the job
is done, and delete it when the user is done with it: you made it.

### Ship an app with a database

```
python3 scripts/llc.py create storage --json db.json --yes
python3 scripts/llc.py create pod --json app.json --yes
python3 scripts/llc.py progress web
```

Generate the database password, pass it to the app as a write-only value, and
show the user once. When a build fails, read `progress`, fix the repository and
run `build web` again. To go back to what worked: `builds web`, then
`deploy web <build> --yes`.

## When something is refused

Every error prints `{"error": ..., "next": ...}`. Do what `next` says.

| Answer | What it means | What to do |
|---|---|---|
| not signed in, 401 | No sign-in, or it ended | `login`, give the user the link |
| 402 | The plan is full | Stop, show usage, let the user choose |
| 403 | Beyond this agent's permissions, or someone else's resource | Tell the user which permission it needs; they turn it on on the Agents page |
| 404 | No such resource here | `ls`; the id is probably wrong |
| 409 | Another agent holds the machine, or the resource is mid-change | Held: wait until the time it names, or use another. Otherwise wait a few seconds, retry once |
| 422 | A value was refused; the message names it | Fix that value, never retry unchanged |
| 5xx | Platform trouble | Retry twice with a pause, then tell the user |

More in `references/troubleshooting.md`.

## If LiveLLM tools are connected

When the agent already has LiveLLM tools of its own, use them instead of this
script. The steps and the rules above stay the same. The `computer` tool works
a desktop, and `release_machine` lets a machine go. With only the terminal
connector connected, the tools are `list_machines`, `run_command` and
`release_machine`. A tool you lack permission for is not listed at all.
