# Annie — AUR package (Arch Linux)

## Install (end user)

Once published on the AUR:

```bash
yay -S annie
# or
paru -S annie
```

**Depends:** `python`, `python-libtorrent`  
**Recommended:** `mpv`

## Local build from the repo

From the cloned project root:

```bash
cd packaging/aur
./build-local.sh
# or: makepkg -si
```

`prepare()` copies the parent repo automatically (no need to re-clone from GitHub).

## AUR publish

1. In [PKGBUILD](PKGBUILD), replace `source=()` with:
   ```bash
   source=("git+https://github.com/CloudDown/annie.git?signed#commit=YOUR_COMMIT")
   sha256sums=('SKIP')
   ```
2. Bump `pkgrel` if needed.

```bash
cd packaging/aur
makepkg --printsrcinfo > .SRCINFO
```

3. Push to an AUR `annie` package (AUR account required).

## Notes

- `pip install` does **not** ship `libtorrent` in the package: use system `python-libtorrent`.
- Unit tests (`check()`) stay in the source tarball.
