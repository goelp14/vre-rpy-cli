# Publish to GitHub

Requires [GitHub CLI](https://cli.github.com/) authenticated (`gh auth login`).

```bash
cd /path/to/vre-rpy-cli
export PATH="$HOME/.local/bin:$PATH"
# first time only:
#   gh repo create vre-rpy-cli --public --source=. --remote=origin --push \
#     --description "ACC .rpy → AI-labeled laps + MoTeC i2 workspace"
git push -u origin HEAD
```

Do **not** commit: personal Documents paths, replay fixtures with real driver sessions, or reverse-engineering notes/addresses.
