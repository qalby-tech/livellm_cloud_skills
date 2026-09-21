# Apps

An app runs a container and gets a public HTTPS address. It comes either from an
image you name, or from a Git repository the platform builds for you.

## From an image

`app.json`:

```json
{
  "id": "web",
  "image": "nginx:1.27",
  "cpu": "500m",
  "memory": "512Mi",
  "ports": [{ "name": "http", "port": 80 }]
}
```

```
python3 scripts/llc.py create pod --json app.json --yes
python3 scripts/llc.py wait web
python3 scripts/llc.py connect web
```

`connect` lists each port's address. A private image needs a login:
`"imageAuth": {"username": "...", "password": "..."}`. The password is stored
write-only and never comes back.

## From a repository

```json
{
  "id": "web",
  "source": { "git": { "url": "https://github.com/acme/app", "ref": "main" } },
  "ports": [{ "name": "http", "port": 8080 }]
}
```

The repository needs a Dockerfile. A private repository takes a token in the
same block: `"source": {"git": {...}, "gitAuth": {"token": "..."}}`, write-only
like every other secret here.

Creating it starts the first build. Follow it:

```
python3 scripts/llc.py progress web
```

A build that works goes live by itself. Later:

```
python3 scripts/llc.py build web      # build the current code again
python3 scripts/llc.py builds web     # what has been built, with commits
python3 scripts/llc.py deploy web 0a22de73 --yes   # run an earlier build again
```

Rolling back is just running an older build again. Say which one you picked and
why. The app keeps its recent builds; they count against the plan's disk.

## Apps that work together

Most real software is several apps: a site, a worker, a database, a cache. Put
them in one **stack** and they find each other by plain name, the way a compose
file writes it:

```json
{ "id": "shop-db",  "stack": "shop", "hostname": "db",  "image": "postgres:17",
  "ports": [{ "name": "pg", "port": 5432, "internal": true }],
  "storage": { "size": "10Gi", "mountPath": "/var/lib/postgresql/data" } }
{ "id": "shop-web", "stack": "shop", "hostname": "web", "image": "ghcr.io/acme/shop:1.4",
  "dependsOn": ["shop-db"],
  "env": [{ "name": "DATABASE_HOST", "value": "db" }],
  "ports": [{ "name": "http", "port": 3000 }] }
```

- `stack` groups them; `hostname` is the name the others use (`db:5432`). Names
  belong to the stack, so two stacks can both have a `db`. Any port works
  between them, declared or not.
- `"internal": true` on a port means no public address: it answers inside the
  workspace only, and may be any TCP protocol. Give every database, cache and
  queue an internal port — never a public one. `connect` shows where it answers.
- `dependsOn` lists what an app needs first. It starts once each one's first
  port accepts a connection, and none of them can be deleted while it is listed
  (delete the dependent app first). Apps that wait for each other in a loop are
  refused.

### From a compose file

If the user has a `compose.yaml`, don't translate it by hand:

```
python3 scripts/llc.py compose compose.yaml --stack shop          # plans, creates nothing
python3 scripts/llc.py compose compose.yaml --stack shop --yes    # creates every app at once
```

The plan lists the apps (named `<stack>-<service>`), `notes` on anything chosen
for you or left out, `variables` the file needs values for (`--var NAME=value`)
and `problems` that block it. Show the user the apps and the notes before
`--yes`. Things worth knowing:

- A published port (`"8080:80"`) becomes a public HTTPS address on the
  container's port; `expose` becomes an internal port; a published database port
  stays internal.
- A service built from source (`build:`) needs `--repo URL [--ref main]` — the
  repository the compose file lives in. A private one reads its token from the
  `LIVELLM_GIT_TOKEN` environment variable.
- A service keeps one volume (5Gi unless you create the apps yourself); files
  mounted from the user's machine are left out — say so.
- It is all or nothing: if one app is refused, none is created.

For a database the user cares about, prefer a managed one
(`references/databases.md`) over a `postgres` image in a stack: it has backups.

## Settings the app reads

```json
"env": [{ "name": "LOG_LEVEL", "value": "info" }],
"secretEnv": [{ "name": "DATABASE_URL", "value": "postgres://..." }]
```

`env` is plain and visible. `secretEnv` is write-only: the value is kept out of
sight and can't be read back, so put passwords and connection strings there.
Send a new value to change it; leave the value out to keep the stored one.

## Who can reach it

An HTTP port becomes a public HTTPS address. Unless the user wants a public
site, protect it when you create it:

```json
"ports": [{
  "name": "http", "port": 8080,
  "access": {
    "allowCIDRs": ["203.0.113.0/24"],
    "auth": "basic",
    "basicUsers": [{ "username": "acme", "password": "GENERATE-ONE" }]
  }
}]
```

Passwords here are write-only too. When you change who may enter, send every
user's password again; sending some and not others is refused.

## Keeping data

`"storage": {"size": "10Gi", "mountPath": "/data"}` gives the app a disk that
survives restarts. Databases
belong in a database, not in an app's disk: see `references/databases.md`.

## When it goes wrong

- **The build failed.** `progress web` shows the stage and the error. Fix the
  repository, then `build web`. Don't change unrelated settings to force it.
- **The address answers 404 or 502.** The app may still be starting: `wait web`.
  Check the port number matches what the program listens on.
- **It built and started but doesn't work.** `logs web` prints the last log
  lines with each container's state, restarts, processor and memory use. Read
  it before guessing; quote the line that explains it when you report back.
- **It keeps restarting.** `logs web` shows the restart count — usually a crash
  on boot, and the reason is in the last lines before each restart.
- **A value was refused (422).** The message names the field. Fix that one.
- **The app needs a password you generated.** Show it to the user once; it can't
  be read back.
