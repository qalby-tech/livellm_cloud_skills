# Databases

Postgres for data an app keeps, Redis for caches and queues. Both are managed:
the platform keeps them running and takes the backups you ask for. You can
back up now, restore a backup into a new database, and restart one.

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

## Connecting

`connect db` prints the addresses. Apps in the same workspace use the private
one, which never leaves the platform. Ask for the public address only when the
user needs to reach the database from outside:

```json
"network": { "expose": true }
```

The public address is `<id>-<workspace>.cloud.live-llm.com`: Postgres on port
5432, Redis on port 6380. Both speak TLS only: `sslmode=require` for Postgres,
`rediss://` for Redis. `connect db` prints the exact address. Give an app its
connection string as a write-only setting:

```json
"secretEnv": [{ "name": "DATABASE_URL", "value": "postgres://app:PASSWORD@HOST:5432/app?sslmode=require" }]
```

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
database. Point the app at it only when the user says so, and restore only
when the user asked for it.

## Care

- Never put a password in `env`, a repository, a log line or a chat message that
  isn't the one-time handover.
- Changing the password: send a new one; the old one stops working, so update
  every app that uses it in the same breath.
- Deleting a database deletes its data. Only ever delete one you created, and
  say so first.
- Redis keeps its keys on disk across restarts, but it has no backups: keep
  in it only what the app can rebuild.
- `restart db --yes` restarts a database. Apps lose their connections for a
  moment, even with three instances; ask first if you didn't create it.

## When it goes wrong

- **It stays `starting`.** Postgres takes a minute to come up. `wait db`, then
  report what the status said.
- **The app can't connect.** Check the address you used: apps in the workspace
  need the private one. From outside, the database must be exposed and the
  connection must use TLS.
- **Password refused.** It was set at creation and can't be read back. The user
  can set a new one; every app then needs the new value.
