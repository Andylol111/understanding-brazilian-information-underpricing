# Always-on host

The long-running jobs (forward-test recorder, TSE BU-web watcher) run on `oracle-slotbank`, an Oracle
Cloud Always Free ARM VM (2 cores / 12 GB, Ubuntu 22.04) shared with other services. brunder runs as its
own user (`brunder`, home `/srv/brunder`) under systemd, capped and at low CPU and IO priority so the
host's other services keep priority. CPU-heavy work (rehearsals, replays) stays on the laptop.

| unit | job | caps |
|---|---|---|
| `brunder-live.service` | `brunder live run prereg/forward_v1.toml` | 40% of one core, 2 GB, nice 10, idle IO |
| `brunder-bweb.service` | `brunder bweb 2026 2 --wait` | 50% of one core, 3 GB, nice 15, idle IO |

Dry run of the watcher's parse under these exact caps (2026-10-27, São Paulo first-round file, the
largest state, presidential + governor rows): 1.4 GB zip, 1.43 M rows kept, **peak RSS 1.32 GB** (cap
3 GB), 54 min (CPU-bound at the 50% cap), no OOM. Disk: 24 GB free; each zip is deleted after parsing.
The runoff files are smaller (one or two offices), so expect a few hours for all 28 states.

The host's `system.slice` shows past OOM kills: Firecrawl's RabbitMQ container (`beam.smp`, 512 MB
limit) on 2026-10-13 and 10-23, and the camoufox container once. None in brunder's units
(`memory.events` oom_kill = 0); systemd re-reports the slice's cumulative counter on `daemon-reload`.

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
