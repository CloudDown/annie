"""Résolution et lancement des lecteurs vidéo (mpv, vlc, ffplay)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from annie.paths import find_program, windows_extended_path

PLAYER_NAMES = ("mpv", "vlc", "ffplay")

_player_cache: str | None = None
_player_exe_cache: str | None = None


def _settings():
    from annie.settings import AnnieSettings

    return AnnieSettings.load()


def _remember_player(kind: str, exe: str) -> str:
    global _player_cache, _player_exe_cache
    _player_cache = kind
    _player_exe_cache = exe
    return kind


def player_binary(player: str) -> str:
    if _player_exe_cache and _player_cache == player:
        return _player_exe_cache
    return find_program(player) or player


def available_players() -> list[str]:
    return [name for name in PLAYER_NAMES if find_program(name)]


def resolve_player(requested: str | None = None) -> str:
    if requested and requested != "auto":
        exe = find_program(requested)
        if exe is None:
            raise RuntimeError(f"player not found: {requested}")
        kind = Path(exe).stem.lower()
        if kind not in set(PLAYER_NAMES):
            kind = Path(requested).stem.lower()
        return _remember_player(kind, exe)
    env = os.environ.get("ANNIE_PLAYER", "").strip().lower()
    if env and env != "auto":
        exe = find_program(env)
        if exe is None:
            raise RuntimeError(f"ANNIE_PLAYER={env} not found")
        return _remember_player(env, exe)
    if (
        _player_cache
        and _player_exe_cache
        and find_program(_player_cache) == _player_exe_cache
    ):
        return _player_cache
    for name in PLAYER_NAMES:
        exe = find_program(name)
        if exe:
            return _remember_player(name, exe)
    raise RuntimeError("no player found — install mpv, vlc, or ffmpeg")


def _mpv_command(
    target: str,
    *,
    ipc_path: Path | None,
    sub_file: Path | None,
    profile: str,
    streaming: bool = False,
) -> list[str]:
    mpv = _settings().player.mpv
    cmd = [player_binary("mpv")]
    gpu_api = mpv.gpu_api
    hwdec = mpv.hwdec
    vo = mpv.vo
    # opengl échoue souvent sous Windows ; d3d11 est le choix sûr.
    if sys.platform == "win32" and gpu_api in {"opengl", ""}:
        gpu_api = "d3d11"
    if profile == "safe":
        gpu_api = "d3d11"
        hwdec = "auto-safe"
        vo = "gpu"
    elif profile == "software":
        gpu_api = "d3d11"
        hwdec = "no"
        vo = "gpu"
    if mpv.force_window:
        cmd.append("--force-window=immediate")
    cmd.extend(
        [
            "--keep-open=no",
            "--no-terminal",
        ]
    )
    if mpv.really_quiet:
        cmd.extend(
            [
                "--really-quiet",
                "--msg-level=all=fatal",
            ]
        )
    readahead_secs = 15 if streaming else 3
    demuxer_max_bytes = "96M" if streaming else "32M"
    cmd.extend(
        [
            "--cache=yes",
            f"--cache-secs={mpv.cache_secs}",
            "--cache-pause=yes",
            "--cache-pause-initial=yes",
            f"--demuxer-readahead-secs={readahead_secs}",
            f"--demuxer-max-bytes={demuxer_max_bytes}",
            "--demuxer-lavf-analyzeduration=8",
            "--demuxer-lavf-probesize=10485760",
            f"--vo={vo}",
            f"--gpu-api={gpu_api}",
            f"--hwdec={hwdec}",
        ]
    )
    if ipc_path is not None:
        cmd.append(f"--input-ipc-server={ipc_path}")
    if sub_file is not None:
        cmd.append(f"--sub-file={windows_extended_path(sub_file.resolve())}")
    cmd.extend(mpv.extra_args)
    cmd.append(target)
    return cmd


def _vlc_command(target: str) -> list[str]:
    vlc = _settings().player.vlc
    cmd = [
        player_binary("vlc"),
        "--intf",
        "dummy",
        "--quiet",
        "--play-and-exit",
        "--no-video-title-show",
        f"--file-caching={vlc.file_caching_ms}",
        f"--network-caching={vlc.network_caching_ms}",
    ]
    cmd.extend(vlc.extra_args)
    cmd.append(target)
    return cmd


def _ffplay_command(target: str) -> list[str]:
    return [
        player_binary("ffplay"),
        "-autoexit",
        "-infbuf",
        "-fflags",
        "+genpts+discardcorrupt",
        "-loglevel",
        "error",
        target,
    ]


def player_command(
    player: str,
    path: Path,
    *,
    ipc_path: Path | None = None,
    sub_file: Path | None = None,
    mpv_profile: str = "default",
    streaming: bool = False,
) -> list[str]:
    target = windows_extended_path(path.resolve())
    if player == "mpv":
        return _mpv_command(
            target,
            ipc_path=ipc_path,
            sub_file=sub_file,
            profile=mpv_profile,
            streaming=streaming,
        )
    if player == "vlc":
        return _vlc_command(target)
    if player == "ffplay":
        return _ffplay_command(target)
    raise RuntimeError(f"unsupported player: {player}")


def player_popen(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
