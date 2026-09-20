# Databases

Postgres for data an app keeps, Redis for caches and queues. Both are managed:
backups, restarts and upgrades are the platform's job.

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
the user once. `"instances": 3` runs Postgres with standbys; `1` is enough for
development.

## Connecting

`connect db` prints the addresses. Apps in the same workspace use the private
one, which never leaves the platform. Ask for the public address only when the
user needs to reach the database from outside:

```json
"network": { "expose": true }
```

Public Postgres speaks TLS; connect with `sslmode=require`. Give an app its
connection string as a write-only setting:

```json
"secretEnv": [{ "name": "DATABASE_URL", "value": "postgres://app:PASSWORD@HOST:5432/app?sslmode=require" }]
```

## Backups

```json
"backup": { "enabled": true, "schedule": "@daily", "maxBackups": 10 }
```

Turn backups on for anything the user cares about. Restoring is done in the
console; say so rather than trying to rebuild data yourself.

## Care

- Never put a password in `env`, a repository, a log line or a chat message that
  isn't the one-time handover.
- Changing the password: send a new one; the old one stops working, so update
  every app that uses it in the same breath.
- Deleting a database deletes its data. Only ever delete one you created, and
  say so first.
- A cache is not storage: anything in Redis can vanish on a restart.

## When it goes wrong

- **It stays `starting`.** Postgres takes a minute to come up. `wait db`, then
  report what the status said.
- **The app can't connect.** Check the address you used: apps in the workspace
  need the private one. From outside, the database must be exposed and the
  connection must use TLS.
- **Password refused.** It was set at creation and can't be read back. The user
  can set a new one; every app then needs the new value.
