# Publish to GitHub

```bash
cd /home/pranav/github/vre-rpy-cli
export PATH="$HOME/.local/bin:$PATH"
gh auth login -h github.com -p https -w
gh repo create vre-rpy-cli --public --source=. --remote=origin --push --description "ACC .rpy → AI-labeled laps + MoTeC i2 workspace"
```
