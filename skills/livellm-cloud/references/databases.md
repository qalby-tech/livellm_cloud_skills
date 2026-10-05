# Databases

Postgres for data an app keeps, Redis for caches and queues. Both are managed:
the platform keeps them running and takes the backups you ask for. You can
back up now, restore a backup into a new database, and restart one.

A database for an app you are making is best made WITH the app, in one step,
and linked to it: the platform makes the password and hands it to the app, so
no password passes through you (`references/apps.md`, "An app with its
databases"). Make one on its own when the user wants a database by itself.

## Create one

`db.json`:

```json
{
  "id": "db",
  "engine": "postgres",
  "version": "17",
  "instances": 1,
  "storageSize": "10Gi",
  "cpu": "1",
  "memory": "1Gi",
  "credentials": { "username": "app", "password": "GENERATE-A-STRONG-ONE" }
}
```

```
python3 scripts/llc.py create storage --json db.json --yes
python3 scripts/llc.py wait db
python3 scripts/llc.py connect db
```

Redis is the same with `"engine": "redis"`.

The id, the engine and the login are required when you create it. The password
is required too, is stored write-only, and can never
be read back. Generate it, put it straight into the app that needs it, and show
the user once. `"instances": 3` runs Postgres with two standby copies that take
over if the main one fails; `1` is enough for development. Redis runs as one
instance only.

## Link it to an app

An app names the databases it uses in its settings, and which of each one's
connection details go into which environment variables:

```json
"databases": [
  { "id": "db",    "env": { "DATABASE_URL": "url" } },
  { "id": "cache", "env": { "REDIS_HOST": "host", "REDIS_PORT": "port", "REDIS_PASSWORD": "password" } }
]
```

| Detail | PostgreSQL | Redis |
|---|---|---|
| `host` | its private address | its private address |
| `port` | `5432` | `6379` |
| `database` | `app` | — |
| `username` | the login's name | — |
| `password` | the password | the password |
| `url` | `postgres://user:password@host:5432/app` | `redis://:password@host:6379` |

- The password and the URL are read from the database's stored login when
  the app starts. They are never in the app's settings, in an answer, or
  anywhere you can read them: don't copy a password into `env` or
  `secretEnv` when a link gives it.
- At most 8 databases per app and 12 variables per database. A variable name
  is letters, digits and `_`, not starting with a digit, and is used once in
  the app, across `env`, `secretEnv` and every link.
- The app starts once its databases accept connections; no `dependsOn`
  needed for them.
- A link lets the app (its whole Composable App) reach the database. Linking
  a database you didn't make is letting a resource in: ask the user first
  (rule 11); an agent or key without the Network permission gets a 403,
  unless the database already lets the whole workspace in.
- `ls` shows each app's links as its settings hold them, and on a database,
  `usedBy`: the apps that link it or wait for it.
- A linked database can't be deleted (409 names the app): take the link out,
  or delete the app, first.
- On an app that exists, `set web --json links.json --yes` with
  `{"pod": {"databases": [...]}}`. The list you send replaces the whole
  list, so copy the links `ls` shows and add the new one.
- **"set a new password for db to link its URL"** (422): the database's
  password was set before links existed, so it can't give `url` yet. Link its
  other details instead (`host`, `port`, `password`, and on PostgreSQL
  `database` and `username`), or, if the user agrees, set a new password once
  (see Care).

## Connecting

`connect db` prints the addresses. Apps in the same workspace use the private
one, which never leaves the platform; a link gives it to them. It answers only
the resources allowed to reach the database: the apps that link it, and what
its `reachableFrom` names (`connect db` shows them under `inside`; a new
database is reached by nothing else). Ask for the public address only when the
user needs to reach the database from outside:

```json
"network": { "expose": true }
```

The public address is `<id>-<workspace>.cloud.live-llm.com`: Postgres on port
5432, Redis on port 6380. Both speak TLS only: `sslmode=require` for Postgres,
`rediss://` for Redis. `connect db` prints the exact address. A password the
platform made is shown to no one: to use the database from outside, agree a
new one with the user (see Care). Apps inside the workspace use a link.

## Backups

Postgres only. Turn them on for anything the user cares about:

```json
"backup": { "enabled": true, "mode": "daily", "keepDays": 10 }
```

- `daily`: a full copy each night.
- `continuous`: the nightly copy plus every change in between, so the database
  can be restored to any minute inside the kept days.
- `manual`: nothing is scheduled; a backup is taken only when asked.

`keepDays` is how many days backups are kept (1 to 365), not a number of
backups. On an existing database, change them with `set db --json
changes.json --yes` and a file like
`{"storage": {"backup": {"enabled": true, "mode": "continuous", "keepDays": 7}}}`.

```
python3 scripts/llc.py backups db          # how it is backed up, and what is kept
python3 scripts/llc.py backup db           # one now, in any mode
NEW_DB_PASSWORD=... python3 scripts/llc.py restore db BACKUP --as db-restored --password-env NEW_DB_PASSWORD --yes
NEW_DB_PASSWORD=... python3 scripts/llc.py restore db BACKUP --as db-restored --password-env NEW_DB_PASSWORD \
    --at 2026-09-25T14:05:00Z --yes
```

`backup db` is refused while one is being taken and when backups are off.
Backups older than `keepDays` leave the store by themselves; there is nothing
to delete.

A restore makes a NEW database (`--as`) from the backup; the original keeps
running untouched. `--at` picks a minute after the backup ended, with
continuous backups. The new database keeps the original's login name and
takes the new password you generate (pass it through an environment
variable, never on the command line). It counts toward the plan like any new
database. It starts with the original's `reachableFrom`. Point the app at
it only when the user says so, and restore only when the user asked for it.

## Care

- Never put a password in `env`, a repository, a log line or a chat message that
  isn't the one-time handover.
- Changing the password: send a new one with the username `ls` shows
  (`set db --json` with
  `{"storage": {"credentials": {"username": "app", "password": "..."}}}`).
  The old one stops working. Linked apps read the new one
  when they restart: `restart` each app in `usedBy`. An app given the
  password by hand needs the new value too.
- Deleting a database deletes its data. Only ever delete one you created, and
  say so first.
- Redis keeps its keys on disk across restarts, but it has no backups: keep
  in it only what the app can rebuild.
- `restart db --yes` restarts a database. Apps lose their connections for a
  moment, even with three instances; ask first if you didn't create it.

## Where it runs

Leave `placement` out and LiveLLM picks the host. To choose, put
`"placement": {"strategy": "region", "region": "<r>"}` or
`{"strategy": "host", "host": "<id>"}` in the file; `llc.py hosts` lists ids and regions.
Changing it (`set` with `{"storage": {"placement": …}}`, `null` for automatic) restarts
the database there; pinned to a host, all its copies run on that host and wait while it is down.
A restore runs where `--host` or `--region` says, automatic without them (never the original's).

## When it goes wrong

- **It stays `starting`.** Postgres takes a minute to come up. `wait db`, then
  report what the status said.
- **409 "still starting, so its location can't change".** Its first start
  never finished. Retrying won't help: with the user's go, delete it and create
  it again (or run the restore again from the same backup).
- **The app can't connect.** Check its links in `ls`, and that the app reads
  the variable names you gave them. An address typed by hand must be the
  private one, and the app must be allowed to reach the database: linked, or
  named in its `reachableFrom` (`reach db`). From outside, the database must be
  exposed and the connection must use TLS.
- **Password refused.** It was set at creation and can't be read back. The user
  can set a new one; every app then needs the new value.
