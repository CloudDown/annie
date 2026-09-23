# Annie on Omarchy

```bash
curl -fsSL https://raw.githubusercontent.com/CloudDown/annie/cursor/initial-release/install.sh | bash
```

On Omarchy this wires the desktop (Super+Shift+I). Already in the repo: `make omarchy`.

Install layout (XDG):

| Path | Role |
|------|------|
| `~/.local/share/annie` | app (clone + `.venv`) |
| `~/.local/bin/annie` | command on PATH |
| `~/.config/annie` | `config.toml` |

Override the app dir with `ANNIE_DIR=/path`.

Same wiring as stock TUIs (`btop`, Docker): `xdg-terminal-exec --app-id=org.omarchy.annie`.

| Surface | Behavior |
|---------|----------|
| `Super+Shift+I` | launch / focus |
| App launcher | `Annie.desktop` |
| Omarchy menu | search `annie` / `anime` |
| Window | float 1100×720 |

`Super+Shift+A` stays ChatGPT.

Do not copy files into `/usr/share/omarchy/`.
