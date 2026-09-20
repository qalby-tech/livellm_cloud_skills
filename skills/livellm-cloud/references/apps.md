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
- **A value was refused (422).** The message names the field. Fix that one.
- **The app needs a password you generated.** Show it to the user once; it can't
  be read back.
