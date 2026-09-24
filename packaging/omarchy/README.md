# Annie on Omarchy

```bash
curl -fsSL https://raw.githubusercontent.com/CloudDown/annie/cursor/initial-release/install.sh | bash
```

On Omarchy this runs `omarchy tui install` + **Super+Shift+I**. From a clone: `make omarchy`.

Install layout (XDG):

| Path | Role |
|------|------|
| `~/.local/share/annie` | app (clone + `.venv`) |
| `~/.local/bin/annie` | command on PATH |
| `~/.config/annie` | `config.toml` |

Override the app dir with `ANNIE_DIR=/path`.

| Surface | Behavior |
|---------|----------|
| `Super+Shift+I` | launch / focus (`TUI.float`) |
| App launcher | via `omarchy-tui-install` |
| Window | Omarchy stock float |

`Super+Shift+A` stays ChatGPT. Terminal colors follow the Omarchy theme (ANSI 16).

Do not copy files into `/usr/share/omarchy/`.
