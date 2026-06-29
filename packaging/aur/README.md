# Annie — paquet AUR (Arch Linux)

## Installation (utilisateur)

Une fois publié sur l’AUR :

```bash
yay -S annie
# ou
paru -S annie
```

**Dépendances :** `python`, `python-libtorrent`, `fzf`  
**Recommandé :** `mpv`

## Build local depuis le dépôt

Depuis la racine du projet clone :

```bash
cd packaging/aur
./build-local.sh
# ou : makepkg -si
```

Le `prepare()` copie automatiquement le dépôt parent (pas besoin de re-cloner GitHub).

## Publication AUR

1. Dans [PKGBUILD](PKGBUILD), remplacer `source=()` par :
   ```bash
   source=("git+https://github.com/CloudDown/annie.git?signed#commit=VOTRE_COMMIT")
   sha256sums=('SKIP')
   ```
2. Mettre à jour `pkgrel` si besoin.

```bash
cd packaging/aur
makepkg --printsrcinfo > .SRCINFO
```

3. Pousser sur un dépôt AUR `annie` (compte AUR requis).

## Notes

- `pip install` **n’installe pas** `libtorrent` dans le paquet : c’est `python-libtorrent` système.
- Les tests unitaires (`check()`) restent dans le tarball source.
