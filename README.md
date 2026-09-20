# LiveLLM Cloud skills

Skills that let your AI agent use [LiveLLM Cloud](https://live-llm.com): open a real
browser and log into sites while you watch, run jobs on Linux machines, work in Ubuntu
or Windows desktops, deploy apps and create databases. You stay in charge: every agent
signs in with your approval, and you can sign it out at any time.

> **Status:** early. Sign-in, browsers, machines, apps and databases work; the skill
> is still being tested for how reliably it loads. Expect small changes before 1.0.

## Skills

| Skill | What it does |
|---|---|
| [`livellm-cloud`](skills/livellm-cloud) | Browsers, machines, desktops, apps and databases on LiveLLM Cloud |

## Install

The skills follow the open [Agent Skills](https://agentskills.io) standard, so they work
in any agent that supports it.

**Claude Code**

```
/plugin marketplace add qalby-tech/livellm_cloud_skills
/plugin install livellm-cloud@livellm
```

**Claude.ai.** Download `livellm-cloud.zip` from the
[latest release](https://github.com/qalby-tech/livellm_cloud_skills/releases/latest),
then upload it under Settings, Capabilities, Skills.

**Claude API.** Upload the same zip with the skills endpoint (`/v1/skills`), attach it
with `container.skills`, and give the agent a LiveLLM API key.

**Codex**

```
codex plugin marketplace add qalby-tech/livellm_cloud_skills
```

Then open `/plugins` and install `livellm-cloud`.

**GitHub Copilot**

```
gh skill install qalby-tech/livellm_cloud_skills livellm-cloud
```

**Cursor.** Open Customize, choose From GitHub Repository, and enter
`qalby-tech/livellm_cloud_skills`.

**OpenClaw**

```
openclaw plugins install livellm-cloud --marketplace qalby-tech/livellm_cloud_skills
```

**Gemini CLI**

```
gemini skills install https://github.com/qalby-tech/livellm_cloud_skills.git --path skills/livellm-cloud
```

**Any other agent**

```
npx skills add qalby-tech/livellm_cloud_skills
```


## Signing in

The first time your agent needs LiveLLM, it shows you one link. Open it, check which
agent is asking and what it may do, and click Allow. The agent signs in by itself after
that and renews its access quietly. You can see and sign out every agent in the
LiveLLM console.

For CI and other runs with no person present, set `LIVELLM_API_KEY` to an API key from
the console. For a self-hosted LiveLLM, set `LIVELLM_API_URL` to its address.

## Contributing

- Each skill is one folder in `skills/`, named in kebab-case, with the same `name` in its
  `SKILL.md`. Skills are siblings; they don't nest.
- A skill must work on its own, because each one is installed and uploaded separately.
  A script that several skills need is copied into each of them, and the check below
  fails if the copies differ.
- Keep people's documentation here, not inside a skill folder.
- Add a new skill to the `skills` list in `.claude-plugin/marketplace.json`, and copy
  that file to `.cursor-plugin/marketplace.json`.
- Run `python3 tools/validate.py` before pushing. CI runs it too.
- Trigger tests live in `tests/<skill>/`.
- To release, follow the steps at the top of [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)
