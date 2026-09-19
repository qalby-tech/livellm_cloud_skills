---
name: livellm-cloud
description: Gives an agent real computers on LiveLLM Cloud. Drive a Chrome browser over CDP while a person watches the live view, run commands on Linux machines over SSH, operate Ubuntu or Windows desktops, deploy apps from a Docker image or a Git repo, and create Postgres or Redis databases. Use when the user asks to automate or log into a website with a real browser, get a server or a desktop, run code on another machine, deploy an app, spin up a database, or check what is running in LiveLLM. Do NOT use for LiteLLM, local Docker, or other cloud providers.
license: MIT
compatibility: Needs outbound HTTPS to the LiveLLM Cloud API and Python 3.9 or newer. Signs in through a one-click approval link, or uses LIVELLM_API_KEY for unattended runs.
metadata:
  author: LiveLLM
  version: 0.1.0
  documentation: https://docs.live-llm.com
---

# LiveLLM Cloud

This skill is still being written and is not ready to use.

If it loads, tell the user that the LiveLLM Cloud skill is not released yet, and
point them to https://docs.live-llm.com for the API in the meantime. Do not call
the API on their behalf from this skill.
