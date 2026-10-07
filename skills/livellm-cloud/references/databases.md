# Databases

Postgres for data an app keeps, Redis for caches and queues, and object
storage (S3) for files: uploads, media, exports, anything an app keeps in
buckets. All three are managed: the platform keeps them running. Postgres
takes the backups you ask for; you can back up now, restore a backup into a
new database, and restart one. Object storage keeps one copy and has no
backups yet ("Object storage (S3)", below).

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

Redis is the same with `"engine": "redis"`. Object storage is
`"engine": "s3"`, with keys instead of a login ("Object storage (S3)",
below).

The id, the engine and the login are required when you create it (an object
storage may leave the username out). The password is required too, is stored
write-only, and can never be read back. Generate it, put it straight into the
app that needs it, and show the user once. `"instances": 3` runs Postgres with
two standby copies that take over if the main one fails; `1` is enough for
development. Redis runs as one instance only.

## Link it to an app

An app names the databases it uses in its settings, and which of each one's
connection details go into which environment variables:

```json
"databases": [
  { "id": "db",    "env": { "DATABASE_URL": "url" } },
  { "id": "cache", "env": { "REDIS_HOST": "host", "REDIS_PORT": "port", "REDIS_PASSWORD": "password" } }
]
```

| Detail | PostgreSQL | Redis | Object storage (S3) |
|---|---|---|---|
| `host` | its private address | its private address | its private address |
| `port` | `5432` | `6379` | `9000` |
| `database` | `app` | — | — |
| `username` | the login's name | — | — |
| `password` | the password | the password | — |
| `url` | `postgres://user:password@host:5432/app` | `redis://:password@host:6379` | — |
| `endpoint` | — | — | `http://<private address>:9000` |
| `region` | — | — | `us-east-1` |
| `bucket` | — | — | `app` |
| `accessKey` | — | — | the access key |
| `secretKey` | — | — | the secret key |

A detail a database doesn't have is refused (422, "… has no url").

- The password, the URL and an object storage's secret key are read from the
  database's stored login when the app starts. They are never in the app's settings, in an answer, or
  anywhere you can read them: don't copy a password into `env` or
  `secretEnv` when a link gives it.
- At most 8 databases per app and 0 to 12 variables per database. A variable
  name is letters, digits and `_`, not starting with a digit, and is used once
  in the app, across `env`, `secretEnv` and every link.
- The app starts once the databases it takes variables from accept
  connections; no `dependsOn` needed for them.
- A link with no variables, `{ "id": "cache" }`, only lets the app reach the
  database: no variable, no wait, and adding or taking it out never restarts
  the app. `python3 scripts/llc.py link web cache --yes` makes one on an app
  that exists, keeping its other links; `--remove` takes links out (one with variables
  too: they leave the app and it restarts, so say so when you ask). Its
  answer says when the app still reaches the database another way.
- A link is the only way anything reaches a database: it lets the app (its
  whole Composable App) in. A link is letting a resource in unless this agent
  or key made both the app and the database (now or earlier). Otherwise ask
  the user first (rule 11): an agent or key without the Network permission
  gets a 403. Linking a database you made from an app you didn't make counts
  too.
- `ls` shows each app's, machine's and Desktop App's links as its settings
  hold them, and on a database, `usedBy`: what links it or waits for it.
- A linked database can't be deleted (409 names what links it): take the link
  out, or delete what links it, first.
- To change the variables of an app that exists, `set web --json links.json
  --yes` with `{"pod": {"databases": [...]}}`. The list you send replaces the
  whole list, so copy the links `ls` shows and change what the user asked.
- **"set a new password for db to link its URL"** (422): the database's
  password was set before links existed, so it can't give `url` yet. Link its
  other details instead (`host`, `port`, `password`, and on PostgreSQL
  `database` and `username`), or, if the user agrees, set a new password once
  (see Care).

## Link it to a machine or a Desktop App

A machine or a Desktop App that needs a database (a job that loads data, a
desktop tool that opens it) links it too, to reach it. It gets no variables:
give the program on it the address `reach db` lists and a login.

```
python3 scripts/llc.py link runner db --yes
```

In a create file: `"databases": [{ "id": "db" }]` (at most 8). Ask the user
first unless you made both (rule 11).

## Connecting

`connect db` prints the addresses. Apps in the same workspace use the private
one, which never leaves the platform; a link gives it to them. It answers only
what links the database: apps, machines and Desktop Apps (`reach db` shows
them); nothing else in the workspace reaches it, and a database takes no
`reachableFrom`. Ask for the public address only when the user needs to reach
the database from outside:

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
database. Nothing reaches it until something links it. Point the app at it
(link it in place of the original) only when the user says so, and restore
only when the user asked for it.

## Object storage (S3)

An S3 server of the workspace's own, on its own disk that can grow. It starts
with one bucket, `app`. Apps use it like any S3 service, with path-style
addressing (an SDK setting: `forcePathStyle`, `addressing_style: path`).

`files.json`:

```json
{
  "id": "files",
  "engine": "s3",
  "storageSize": "20Gi",
  "cpu": "500m",
  "memory": "1Gi",
  "credentials": { "username": "filesapp", "password": "GENERATE-A-STRONG-ONE" }
}
```

```
python3 scripts/llc.py create storage --json files.json --yes
python3 scripts/llc.py wait files
python3 scripts/llc.py connect files
```

- `credentials.username` is the access key (3 to 31 lowercase letters, digits
  or `_`, starting with a letter or `_`); leave it out and one is made.
  `credentials.password` is the secret key: 8 to 128 characters, no space at
  either end, stored write-only like a database password. It can never be
  read back: generate it, hand it to what needs it, and show the user once.
- `storageSize` is at least `1Gi` (default `5Gi`); it can grow, never shrink.
  `instances` stays `1` and `version` stays `"1"` (422 otherwise).
- Region `us-east-1`. More buckets: make them in the RustFS console or with
  any S3 tool (`aws s3 mb s3://reports --endpoint-url ENDPOINT`, path-style).
- `connect files` prints the access key, the region, the bucket, the private
  endpoint, and the public and console addresses when they are on (never the
  secret key). `ls` names its engine (`s3`).

**Link it to an app.** The usual variables:

```json
"databases": [
  { "id": "files", "env": { "AWS_ENDPOINT_URL_S3": "endpoint", "AWS_ACCESS_KEY_ID": "accessKey",
                            "AWS_SECRET_ACCESS_KEY": "secretKey", "AWS_REGION": "region",
                            "S3_BUCKET": "bucket" } }
]
```

The secret key reaches the app from the stored login, never through you. An
object storage made with its app (`create apps`, `"databases": [{"id":
"files", "engine": "s3"}]`) gets keys nobody sees. Machines and Desktop Apps
link it with no variables, to reach it: give the program there the address
`reach files` lists (port `9000`) and keys the user hands it. A link is the
only way anything in the workspace reaches it (rule 11), on 9000 only.

**Links carry the server's own keys** (full control: every bucket, every
file, its users). For an app the user trusts less, a key of its own limited
to one bucket is made in the RustFS console. The console is a public sign-in
page (below): turn it on only if the user agrees, or have them make the key
with a MinIO admin client (`mc admin user add`) on a machine that links the
object storage. Give the key as `secretEnv` instead of a link's
`accessKey`/`secretKey` (the app still links it to reach it). A new secret
key doesn't remove users or keys made in the console: check them there after
one.

**From outside.** Turn on either address only when the user asks to reach
the object storage from outside the workspace.
`"network": { "expose": true }` gives an HTTPS S3 address,
`https://<id>-<workspace>.cloud.live-llm.com` (port 443, path-style).
`"adminConsole": true` turns on the RustFS console at
`https://<id>-admin-<workspace>.cloud.live-llm.com/rustfs/console/`: it signs
in with the access key and the secret key, and manages buckets, files, keys
and policies. Its address also answers S3 to anyone holding the keys, even
without `expose`. `"network": { "allowlist": ["203.0.113.0/24"] }` limits
both addresses; it is the only address gate (a bucket policy's
`aws:SourceIp` isn't supported). Turning the console on takes the secret key
in the same `set` (the current one or a new one); a key nobody saw means
agreeing a new one with the user. That save restarts the server for a few
seconds, even with the current key: tell the user first.

**One copy, no backups.** Deleting a file, a bucket or the object storage is
final. A `backup` that is on is refused (422); `backups`, `backup` and
`restore` are refused too. Keep in it only what the user accepts losing, or
copies of files kept elsewhere, and say so when you make one.

**Care.**
- Never print, log or paste the secret key outside the one-time handover.
  Delete the file you wrote it into once the save succeeds (`create` and
  `set` remind you); keep it only to resend after a 502.
- Ask the user before deleting a bucket or files you didn't make.
- A new secret key (`set files --json` with
  `{"storage": {"credentials": {"username": "filesapp", "password": "..."}}}`)
  restarts the server for a few seconds; the old key stops working, and apps
  in `usedBy` need a `restart` to read the new one. Changing its CPU, memory,
  placement or plan restarts it too, and so does turning the console on (even
  with the current key); turning the console off restarts nothing.
- It makes no connection out: bucket notifications, replication or tiering
  to somewhere else don't work.
- A LiveLLM without object storage refuses `"engine": "s3"` (422): tell the
  user.

## Care

- Never put a password in `env`, a repository, a log line or a chat message that
  isn't the one-time handover. Delete the file you wrote it into once it is
  sent.
- Changing the password: send a new one with the username `ls` shows
  (`set db --json` with
  `{"storage": {"credentials": {"username": "app", "password": "..."}}}`).
  The old one stops working. Linked apps read the new one
  when they restart: `restart` each app in `usedBy`. An app given the
  password by hand needs the new value too.
- Deleting a database deletes its data. Only ever delete one you created, and
  say so first.
- Turning a database's admin console on (`"adminConsole": true`) takes the
  password in the same `set`: the console signs in with it.
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
  private one, and the app must link the database (`reach db` lists what
  does; `link web db --yes`, with the user's agreement). From outside, the database must be
  exposed and the connection must use TLS.
- **Password refused.** It was set at creation and can't be read back. The user
  can set a new one; every app then needs the new value.
