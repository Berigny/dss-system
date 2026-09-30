# BOOTSTRAP: dss-system ai-copilots

**Audience:** An agent operating this checkout.

**Goal:** Keep Cursor and VS Code with GitHub Copilot pointed at the canonical
skills under `ai-copilots/`. Never copy skill bodies into an IDE discovery
directory.

## Resolve the module root

```bash
MOD="$(pwd)"
test -f "$MOD/Makefile" && test -d "$MOD/ai-copilots"
echo "Module Dir: $MOD"
```

## Wire Cursor and VS Code with GitHub Copilot

Run from the workspace root that the IDE opens (this repository root):

```bash
MOD="$(pwd)"
test -d "$MOD/ai-copilots" || {
  echo "cannot resolve canonical ai-copilots tree" >&2
  exit 1
}

mkdir -p .cursor/skills .github/skills
for skill in \
  dss-eval-operator
do
  ln -sfn "$MOD/ai-copilots/skills/$skill" ".cursor/skills/$skill"
  ln -sfn "$MOD/ai-copilots/skills/$skill" ".github/skills/$skill"
done
```

On Windows, use directory junctions instead of `ln -sfn`. If a discovery path
contains a real directory or stale copied skill, stop and ask before replacing
it.

## Verify

```bash
MOD="$(pwd)"
for ide in .cursor .github
do
  for skill in \
    dss-eval-operator
  do
    test -f "$ide/skills/$skill/SKILL.md" || {
      echo "missing $ide/skills/$skill/SKILL.md" >&2
      exit 1
    }
  done
done
test -f "$MOD/ai-copilots/BOOTSTRAP.md"
```
