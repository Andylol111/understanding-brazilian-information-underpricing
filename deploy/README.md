# Always-on host

The long-running jobs (forward-test recorder, TSE BU-web watcher) run on `oracle-slotbank`, an Oracle
Cloud Always Free ARM VM (2 cores / 12 GB, Ubuntu 22.04) shared with other services. brunder runs as its
own user (`brunder`, home `/srv/brunder`) under systemd, capped and at low CPU and IO priority so the
host's other services keep priority. CPU-heavy work (rehearsals, replays) stays on the laptop.

| unit | job | caps |
|---|---|---|
| `brunder-live.service` | `brunder live run prereg/forward_v1.toml` | 40% of one core, 2 GB, nice 10, idle IO |
| `brunder-bweb.service` | `brunder bweb 2026 2 --wait` | 50% of one core, 3 GB, nice 15, idle IO |

Setup (once, as a sudoer on the host):

```sh
sudo useradd -r -m -d /srv/brunder -s /bin/bash brunder
sudo -u brunder sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
# read-only deploy key: generate as brunder, add with `gh repo deploy-key add`
sudo -u brunder git clone git@github.com:Andylol111/understanding-brazilian-information-underpricing.git /srv/brunder/repo
sudo -u brunder sh -c 'cd /srv/brunder/repo && ~/.local/bin/uv sync --frozen'
sudo cp deploy/*.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now brunder-live brunder-bweb
```

Update: `sudo -u brunder git -C /srv/brunder/repo pull && sudo systemctl restart brunder-live`.
Logs: `journalctl -u brunder-live -f`. Data lives in `/srv/brunder/repo/data` (gitignored).
Moving the recorder between hosts: stop it on the old host, copy `data/live/`, start it on the new one,
and log the handoff in `prereg/NOTES.md`.
