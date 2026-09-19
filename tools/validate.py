#!/usr/bin/env python3
"""Check every skill in skills/ against the Agent Skills rules and this repo's own.

Run from the repository root:  python3 tools/validate.py
Needs PyYAML. Exits non-zero and lists every problem found.
"""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
# Cursor only installs from its own marketplace file; it must match Claude's.
MARKETPLACE_COPIES = [ROOT / ".cursor-plugin" / "marketplace.json"]
# The open standard's frontmatter keys; its validator rejects any other.
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED = ("claude", "anthropic")
# Words that name how the platform is built rather than what people use.
# The skill speaks of workspaces, resources, machines, browsers, apps and databases.
INTERNAL_WORDS = ("tenant", "namespace", "kubernetes", "k8s", "kubectl", "helm", "kubevirt")
TEXT_SUFFIXES = {".md", ".py", ".sh", ".mjs", ".js", ".ts", ".txt", ".yaml", ".yml", ".json"}
MAX_BODY_WORDS = 5000

problems: list[str] = []


def problem(where: str, what: str) -> None:
    problems.append(f"{where}: {what}")


def split_frontmatter(text: str):
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 5 :]


def check_skill(folder: Path) -> str | None:
    where = f"skills/{folder.name}"
    if not NAME_RE.match(folder.name) or len(folder.name) > 64:
        problem(where, "folder name must be kebab-case: lowercase letters, digits and single hyphens, at most 64 characters")
    names = {p.name for p in folder.iterdir()}
    if "SKILL.md" not in names:
        problem(where, "SKILL.md is missing (the name is case-sensitive)")
        return None
    if "README.md" in names:
        problem(where, "no README.md inside a skill folder; put people's docs in the repository README")

    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    raw, body = split_frontmatter(text)
    if raw is None:
        problem(where, "SKILL.md must start with YAML frontmatter between --- lines")
        return None
    if "<" in raw or ">" in raw:
        problem(where, "frontmatter must not contain angle brackets")
    try:
        fm = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        problem(where, f"frontmatter is not valid YAML: {e}")
        return None

    extra = set(fm) - SPEC_KEYS
    if extra:
        problem(where, f"frontmatter keys outside the standard: {', '.join(sorted(extra))}")
    meta = fm.get("metadata")
    if meta is not None and (not isinstance(meta, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in meta.items())):
        problem(where, "metadata must map strings to strings (quote numbers like versions)")
    allowed = fm.get("allowed-tools")
    if allowed is not None and not isinstance(allowed, str):
        problem(where, "allowed-tools must be one space-separated string")

    name = fm.get("name")
    if name != folder.name:
        problem(where, f"name {name!r} must match the folder name")
    if isinstance(name, str) and any(r in name.lower() for r in RESERVED):
        problem(where, "name must not contain 'claude' or 'anthropic'")
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        problem(where, "description is required")
    elif len(desc) > 1024:
        problem(where, f"description is {len(desc)} characters, at most 1024")
    compat = fm.get("compatibility")
    if compat is not None and not (1 <= len(str(compat)) <= 500):
        problem(where, "compatibility must be 1 to 500 characters")
    version = (fm.get("metadata") or {}).get("version")
    if not version:
        problem(where, "metadata.version is required so releases can be tracked")

    words = len(body.split())
    if words > MAX_BODY_WORDS:
        problem(where, f"SKILL.md body is {words} words, keep it under {MAX_BODY_WORDS}")

    # Every bundled file the instructions point at must exist.
    for ref in sorted(set(re.findall(r"\b((?:references|scripts|assets)/[\w./-]+)", body))):
        if not (folder / ref.rstrip(".")).exists():
            problem(where, f"SKILL.md mentions {ref}, which doesn't exist")

    for path in folder.rglob("*"):
        if path.is_symlink():
            problem(f"{where}/{path.relative_to(folder)}", "no symlinks: skills are copied and zipped as plain folders")
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            content = path.read_text(encoding="utf-8", errors="replace").lower()
            for word in INTERNAL_WORDS:
                if re.search(rf"\b{re.escape(word)}s?\b", content):
                    problem(f"{where}/{path.relative_to(folder)}", f"uses the internal word {word!r}")
    return str(version) if version else None


def check_shared_scripts(folders: list[Path]) -> None:
    # Skills must be self-contained, so a shared script is copied into each
    # skill. The copies must stay identical.
    copies: dict[str, dict[str, bytes]] = {}
    for folder in folders:
        for script in (folder / "scripts").glob("*") if (folder / "scripts").is_dir() else []:
            copies.setdefault(script.name, {})[folder.name] = script.read_bytes()
    for script, by_skill in copies.items():
        if len(set(by_skill.values())) > 1:
            problem(f"scripts/{script}", "copies differ between skills: " + ", ".join(sorted(by_skill)))


def check_layout(folders: list[Path]) -> None:
    # A SKILL.md at the root turns the whole repository into one skill for
    # some installers, and Codex skips hidden folders.
    if (ROOT / "SKILL.md").exists():
        problem("SKILL.md", "no SKILL.md at the repository root; skills live in skills/<name>/")
    for folder in folders:
        if folder.name.startswith("."):
            problem(f"skills/{folder.name}", "skill folders must not be hidden")
    original = MARKETPLACE.read_bytes() if MARKETPLACE.exists() else b""
    for copy in MARKETPLACE_COPIES:
        if not copy.exists() or copy.read_bytes() != original:
            problem(str(copy.relative_to(ROOT)), "must be an exact copy of .claude-plugin/marketplace.json")


def check_marketplace(folders: list[Path], versions: dict[str, str]) -> None:
    where = ".claude-plugin/marketplace.json"
    try:
        market = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        problem(where, f"missing or not valid JSON: {e}")
        return
    listed: set[str] = set()
    for plugin in market.get("plugins", []):
        for path in plugin.get("skills", []):
            target = (ROOT / path).resolve()
            if not (target / "SKILL.md").is_file():
                problem(where, f"plugin {plugin.get('name')!r} lists {path}, which has no SKILL.md")
            listed.add(target.name)
    for folder in folders:
        if folder.name not in listed:
            problem(where, f"skills/{folder.name} is not listed in any plugin")
    release = (market.get("metadata") or {}).get("version")
    for skill, version in versions.items():
        if version != release:
            problem(f"skills/{skill}", f"metadata.version {version} differs from the marketplace version {release}")


def main() -> int:
    folders = sorted(p for p in SKILLS.iterdir() if p.is_dir()) if SKILLS.is_dir() else []
    if not folders:
        problem("skills/", "no skills found")
    versions = {}
    for folder in folders:
        v = check_skill(folder)
        if v:
            versions[folder.name] = v
    check_shared_scripts(folders)
    check_layout(folders)
    check_marketplace(folders, versions)
    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} problem(s)")
        return 1
    print(f"{len(folders)} skill(s) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
