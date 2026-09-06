"""CLI Annie."""

from __future__ import annotations

import argparse
import re
import sys

from annie.settings import AnnieSettings
from annie.config import AnnieConfig
from annie.catalog import (
    build_catalog,
    fill_section_gaps,
    resolve_catalog_target,
)
from annie.gather import format_catalog_status, gather_catalog
from annie.parsing import kind_from_options, minimal_label, parse_inline_target
from annie.scoring import pick_best, rank_entry
from annie.types import MediaKind, MediaSection, ResultItem, WatchTarget
from annie import metadata as meta
from annie.nyaa import search
from annie.ui import (
    BackToEpisode,
    BACK_TO_EPISODE,
    C,
    begin_playback_ui,
    copy_magnet,
    EXIT_CANCELLED,
    PLAY_COMPLETED,
    PLAY_INCOMPLETE,
    fzf_install_hint,
    tui_available,
    is_play_completed,
    pick_anime_candidate,
    pick_catalog,
    pick_episode,
    pick_subtitle_language,
    read_query,
    run_search_spinner,
    print_banner,
    print_help,
    print_status,
    parse_prompt_command,
    stylize,
    is_user_cancel,
)
from annie.watch_history import WatchHistory

MOVIE_NUMBER_RE = re.compile(r"\bmovie\s*(?P<num>[1-9])\b", re.I)


def _print_entry(entry, parsed, config: AnnieConfig, *, highlight: bool = False) -> None:
    prefix = stylize(">>", C.GREEN, C.BOLD) if highlight else "  "
    label = minimal_label(parsed)
    threshold = config.ui.seeders_highlight
    seeds = stylize(
        f"[{entry.seeders:>4}S]", C.GREEN if entry.seeders >= threshold else C.YELLOW
    )
    print(
        f"{prefix} {seeds} {stylize(entry.size, C.MUTED):>8}  "
        f"{stylize(label, C.GREEN):28}  {entry.title}"
    )


def _movie_number(title: str) -> int | None:
    match = MOVIE_NUMBER_RE.search(title)
    return int(match.group("num")) if match else None


def _should_direct(options: dict) -> bool:
    if options.get("movie_number"):
        return True
    if options.get("direct"):
        return True
    return False


def _pick_for_options(entries, query: str, options: dict, config: AnnieConfig):
    kind = kind_from_options(options)
    target = WatchTarget(
        query=query,
        season=options.get("season"),
        episode=options.get("episode"),
        kind=kind,
    )
    if target.kind is None and target.episode is not None:
        target = WatchTarget(
            query=query,
            season=target.season,
            episode=target.episode,
            kind=MediaKind.EPISODE,
        )

    movie_number = options.get("movie_number")
    if movie_number:
        ranked = []
        for entry in entries:
            result = rank_entry(entry, WatchTarget(query=query, kind=MediaKind.MOVIE))
            if result is None:
                continue
            score, parsed = result
            number = _movie_number(entry.title)
            if number == movie_number:
                score += 5000
            elif number is not None:
                continue
            ranked.append((score, entry, parsed))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1], ranked[0][2]

    return pick_best(entries, target)


def resolve_anime_for_query(
    query: str, config: AnnieConfig
):
    """Recherche + confirmation TUI sur le thread principal (hors spinner)."""
    if not meta.metadata_enabled(config):
        return None
    try:
        candidates = meta.search_anime(query, config=config)
    except Exception:
        return None
    if not candidates:
        return None
    if (
        config.metadata.confirm_ambiguous
        and sys.stdin.isatty()
        and tui_available()
        and meta.is_ambiguous_pick(candidates, query)
    ):
        picked = pick_anime_candidate(candidates, query)
        if picked is not None:
            return picked
    return meta.pick_candidate(candidates, query)


def _catalog_error(exc: BaseException) -> None:
    print(
        stylize(f"annie: catalog unavailable ({exc}), Nyaa fallback", C.MUTED),
        file=sys.stderr,
        flush=True,
    )


def print_status_line(label: str, seeders: int, release_group: str | None) -> None:
    del seeders, release_group
    begin_playback_ui()
    print(stylize(f"◆ {label}", C.YELLOW, C.BOLD), flush=True)


def _find_section_for_item(
    catalog: list[MediaSection], item: ResultItem
) -> MediaSection | None:
    for section in catalog:
        for candidate in section.choices():
            if candidate.entry.magnet == item.entry.magnet:
                return section
    return None


def _find_section_for_episode(
    catalog: list[MediaSection],
    item: ResultItem,
) -> MediaSection | None:
    episode = item.parsed.episode
    season = item.parsed.season
    if episode is not None:
        for section in catalog:
            if season is not None and section.season not in {season, None}:
                continue
            if episode in section.episodes:
                return section
    return _find_section_for_item(catalog, item)


def _next_episode_item(
    section: MediaSection, item: ResultItem
) -> ResultItem | None:
    episode = item.parsed.episode
    if episode is None:
        return None
    for ep in sorted(section.episodes):
        if ep > episode:
            return section.episodes[ep]
    return None


def _binge_chain(
    section: MediaSection, item: ResultItem, *, max_ahead: int = 16
) -> list[ResultItem]:
    """Épisodes suivants de la saison (tout magnet) pour enchaînement sans relancer mpv."""
    chain: list[ResultItem] = []
    current = item
    while len(chain) < max_ahead:
        nxt = _next_episode_item(section, current)
        if nxt is None:
            break
        chain.append(nxt)
        current = nxt
    return chain


def _resolve_subtitle_lang(
    config: AnnieConfig,
    *,
    cli_lang: str | None = None,
    interactive: bool = False,
) -> str | None | BackToEpisode:
    if not config.subtitles_enabled:
        return None
    if cli_lang:
        return cli_lang
    if config.default_sub_lang:
        return config.default_sub_lang
    if interactive and tui_available():
        result = pick_subtitle_language()
        if result is BACK_TO_EPISODE:
            return BACK_TO_EPISODE
        return result
    return None


def play_item(
    item: ResultItem,
    config: AnnieConfig,
    *,
    keep: bool = False,
    player: str | None = None,
    subtitle_lang: str | None = None,
    series_query: str | None = None,
    mal_titles: tuple[str, ...] = (),
    match_queries: list[str] | None = None,
    interactive_subs: bool = False,
    binge_items: list[ResultItem] | None = None,
    on_episode_done=None,
) -> int:
    file_query = None
    season = item.parsed.season
    episode = item.parsed.episode

    lang = _resolve_subtitle_lang(
        config,
        cli_lang=subtitle_lang,
        interactive=interactive_subs,
    )
    if lang is BACK_TO_EPISODE:
        return -1
    subtitle_query = None
    if lang:
        from annie.subtitles import build_query

        subtitle_query = build_query(
            item,
            series_title=series_query,
            mal_titles=mal_titles,
        )

    file_match_queries = list(
        dict.fromkeys(
            q
            for q in [
                *(match_queries or ()),
                series_query or "",
                *mal_titles,
            ]
            if q and str(q).strip()
        )
    )

    label = minimal_label(item.parsed)
    from annie.stream import play

    return play(
        item.entry.magnet,
        None,
        file_query,
        keep,
        player=config.resolved_player(player),
        episode=episode,
        season=season,
        source_episode=item.parsed.source_episode,
        match_queries=file_match_queries or None,
        seed_while_watching=AnnieSettings.load().seed_while_watching,
        subtitle_lang=lang,
        subtitle_query=subtitle_query,
        listed_seeders=item.entry.seeders,
        on_ui_start=lambda: print_status_line(
            label, item.entry.seeders, item.parsed.release_group
        ),
        binge_items=binge_items,
        on_episode_done=on_episode_done,
        current_item=item,
    )


def try_direct_play(
    raw_query: str,
    config: AnnieConfig,
    *,
    keep: bool = False,
    player: str | None = None,
) -> int | None:
    query, options = parse_inline_target(raw_query)
    if not _should_direct(options):
        return None

    season = options.get("season")
    episode = options.get("episode")
    movie_number = options.get("movie_number")
    if season is not None or episode is not None or movie_number is not None:
        try:
            catalog, catalog_options = gather_catalog(
                raw_query,
                config,
                target_season=season,
                target_kind=kind_from_options(options),
            )
            if catalog_options.get("scope_missed"):
                print_status(
                    format_catalog_status(catalog, catalog_options), kind="warn"
                )
                return 1
            item = resolve_catalog_target(
                catalog,
                season=season or catalog_options.get("season"),
                episode=episode or catalog_options.get("episode"),
                kind=kind_from_options(options),
                movie_number=movie_number,
            )
            if item is not None:
                section = _find_section_for_item(catalog, item)
                return play_item(
                    item,
                    config,
                    keep=keep,
                    player=player,
                    series_query=raw_query,
                    mal_titles=tuple(catalog_options.get("mal_titles", ())),
                    match_queries=(
                        list(section.nyaa_queries) if section is not None else None
                    ),
                    interactive_subs=True,
                )
        except Exception as exc:
            _catalog_error(exc)

    entries = search(query, category=config.category, filter_code=config.filter_code)
    picked = _pick_for_options(entries, query, options, config)
    if picked is None:
        print("  no torrent found.", file=sys.stderr)
        return 1
    entry, parsed = picked
    item = ResultItem(entry=entry, parsed=parsed, score=0.0)
    return play_item(
        item,
        config,
        keep=keep,
        player=player,
        series_query=raw_query,
        interactive_subs=True,
    )


def run_search(
    query: str,
    config: AnnieConfig,
    *,
    season: int | None = None,
    episode: int | None = None,
    movie: bool = False,
    ova: bool = False,
    special: bool = False,
    limit: int | None = None,
    category: str | None = None,
    filter_code: str | None = None,
) -> int:
    entries = search(
        query,
        category=category or config.category,
        filter_code=filter_code or config.filter_code,
    )
    if not entries:
        print("  no results.")
        return 1

    kind = (
        MediaKind.MOVIE
        if movie
        else MediaKind.OVA
        if ova
        else MediaKind.SPECIAL
        if special
        else None
    )
    target = WatchTarget(query=query, season=season, episode=episode, kind=kind)

    if season is not None or episode is not None or movie or ova or special:
        best = pick_best(entries, target)
        if best is None:
            print("  no matching torrent.")
            return 1
        entry, parsed = best
        score = rank_entry(entry, target)
        print(f"\n  best match ({score[0]:.0f} pts)")
        _print_entry(entry, parsed, config, highlight=True)
        return 0

    display_limit = limit if limit is not None else config.catalog.search_results_limit
    catalog = build_catalog(entries, query, skip_recap_movies=config.skip_recap_movies)
    if not catalog:
        print("  no results.")
        return 1

    for section in catalog:
        hint = " · batch recommended" if section.batch_recommended else ""
        print(f"\n  == {section.label}{hint} ==")
        for item in section.choices()[:display_limit]:
            _print_entry(item.entry, item.parsed, config)
    return 0


def run_watch(
    query: str,
    config: AnnieConfig,
    *,
    season: int | None = None,
    episode: int | None = None,
    movie: bool = False,
    ova: bool = False,
    special: bool = False,
    batch: bool = False,
    index: int | None = None,
    query_file: str | None = None,
    keep: bool = False,
    category: str | None = None,
    filter_code: str | None = None,
    player: str | None = None,
    subtitle_lang: str | None = None,
) -> int:
    options = {
        "season": season,
        "episode": episode,
        "movie": movie,
        "ova": ova,
        "special": special,
        "batch": batch,
        "movie_number": None,
        "direct": True,
    }

    if season is not None or episode is not None or movie or ova or special:
        raw_parts = [query]
        if season is not None and episode is not None:
            raw_parts.append(f"s{season:02d}e{episode:02d}")
        elif season is not None:
            raw_parts.append(f"s{season}")
        raw_query = " ".join(raw_parts)
        try:
            catalog, catalog_options = gather_catalog(
                raw_query,
                config,
                category=category,
                filter_code=filter_code,
                target_season=season,
                target_kind=kind_from_options(options),
            )
            if catalog_options.get("scope_missed"):
                print_status(
                    format_catalog_status(catalog, catalog_options), kind="warn"
                )
                return 1
            item = resolve_catalog_target(
                catalog,
                season=season,
                episode=episode,
                kind=kind_from_options(options),
            )
            if item is not None:
                section = _find_section_for_item(catalog, item)
                return play_item(
                    item,
                    config,
                    keep=keep,
                    player=player,
                    subtitle_lang=subtitle_lang,
                    series_query=raw_query,
                    mal_titles=tuple(catalog_options.get("mal_titles", ())),
                    match_queries=(
                        list(section.nyaa_queries) if section is not None else None
                    ),
                )
        except Exception as exc:
            _catalog_error(exc)

    entries = search(
        query,
        category=category or config.category,
        filter_code=filter_code or config.filter_code,
    )
    picked = _pick_for_options(entries, query, options, config)
    if picked is None:
        print("  no torrent found.", file=sys.stderr)
        return 1

    entry, parsed = picked
    label = minimal_label(parsed)
    file_query = query_file
    batch_season = season if parsed.kind == MediaKind.BATCH else parsed.season
    batch_episode = episode if parsed.kind == MediaKind.BATCH else parsed.episode
    if batch_episode is not None:
        file_query = None
    from annie.stream import play

    lang = _resolve_subtitle_lang(config, cli_lang=subtitle_lang)
    subtitle_query = None
    if lang:
        from annie.subtitles import build_query

        subtitle_query = build_query(
            ResultItem(entry=entry, parsed=parsed, score=0.0),
            series_title=query,
        )

    return play(
        entry.magnet,
        index,
        file_query,
        keep,
        player=config.resolved_player(player),
        episode=batch_episode,
        season=batch_season,
        seed_while_watching=AnnieSettings.load().seed_while_watching,
        subtitle_lang=lang,
        subtitle_query=subtitle_query,
        listed_seeders=entry.seeders,
        on_ui_start=lambda: print_status_line(label, entry.seeders, parsed.release_group),
    )


def interactive_loop(config: AnnieConfig) -> int:
    if not tui_available() or not sys.stdout.isatty():
        print_status(
            f"interactive mode requires a TTY — {fzf_install_hint()}", kind="err"
        )
        return 1

    if config.ui.show_banner:
        print_banner()

    while True:
        raw_query = read_query()
        if raw_query is None:
            return 0
        if not raw_query:
            continue

        cmd = parse_prompt_command(raw_query)
        if cmd == "help":
            print_help()
            continue
        if cmd == "settings":
            from annie.config import reload_config
            from annie.settings import reload_settings
            from annie.tui import run_settings

            if run_settings():
                config = reload_config()
                reload_settings()
                print_status("settings saved", kind="ok")
            continue
        if cmd == "quit":
            return 0

        direct = try_direct_play(raw_query, config)
        if direct is not None:
            if direct != 0 and not is_user_cancel(direct):
                print_status("playback interrupted", kind="warn")
            continue

        try:
            query, options = parse_inline_target(raw_query)
            # Un seul pick si vraiment ambigu — pas de menu AllAnime en plus.
            preselected = resolve_anime_for_query(query, config)
            catalog, options = run_search_spinner(
                query,
                lambda: gather_catalog(
                    raw_query,
                    config,
                    target_season=options.get("season"),
                    target_kind=kind_from_options(options),
                    confirm_anime=False,
                    preselected=preselected,
                ),
            )
        except KeyboardInterrupt:
            continue
        except Exception as exc:  # noqa: BLE001
            print_status(str(exc), kind="err")
            continue

        if not catalog:
            note = format_catalog_status([], options)
            print_status(note if options.get("scope_missed") else "no results", kind="warn")
            continue

        print_status(format_catalog_status(catalog, options), kind="info")

        inline_episode = options.get("episode")
        inline_season = options.get("season")
        if inline_episode is not None:
            for section in catalog:
                if inline_season is not None and section.season not in {
                    inline_season,
                    None,
                }:
                    continue
                if section.episodes.get(inline_episode) is not None:
                    continue
                fill_section_gaps(
                    section,
                    search=search,
                    category=config.category,
                    filter_code=config.filter_code,
                    skip_recap_movies=config.skip_recap_movies,
                    target_episode=inline_episode,
                )

        def on_section(section) -> None:
            fill_section_gaps(
                section,
                search=search,
                category=config.category,
                filter_code=config.filter_code,
                skip_recap_movies=config.skip_recap_movies,
                target_episode=inline_episode,
            )

        binge_season = options.get("season")
        next_episode = options.get("episode")
        require_episode_pick = False
        watch_history = WatchHistory.load()

        while True:
            picked = pick_catalog(
                catalog,
                season=binge_season,
                episode=next_episode,
                kind=kind_from_options(options),
                on_section=on_section,
                require_episode_pick=require_episode_pick,
                watch_history=watch_history,
            )
            next_episode = None
            require_episode_pick = False

            if picked is None:
                binge_season = None
                break

            action, item = picked
            if action == "ctrl-o":
                if copy_magnet(item.entry.magnet):
                    print_status("magnet copied", kind="ok")
                else:
                    print_status("clipboard unavailable", kind="warn")
                continue

            session_sub_lang = _resolve_subtitle_lang(config, interactive=True)
            if session_sub_lang is BACK_TO_EPISODE:
                require_episode_pick = True
                continue

            code = 0
            active_section = _find_section_for_episode(catalog, item)
            while True:
                binge_chain: list[ResultItem] = []
                try:
                    section = active_section or _find_section_for_episode(
                        catalog, item
                    )
                    binge_chain = (
                        _binge_chain(section, item) if section is not None else []
                    )

                    def _mark_done(done_item: ResultItem, *, _sec=section) -> None:
                        if _sec is not None:
                            watch_history.mark_item(_sec, done_item)

                    code = play_item(
                        item,
                        config,
                        series_query=raw_query,
                        mal_titles=tuple(options.get("mal_titles", ())),
                        match_queries=(
                            list(section.nyaa_queries) if section is not None else None
                        ),
                        interactive_subs=False,
                        subtitle_lang=session_sub_lang
                        if isinstance(session_sub_lang, str)
                        else None,
                        binge_items=binge_chain or None,
                        on_episode_done=_mark_done if section is not None else None,
                    )
                except KeyboardInterrupt:
                    code = EXIT_CANCELLED
                    break
                except Exception as exc:  # noqa: BLE001
                    print_status(str(exc), kind="err")
                    code = 1
                    break
                if code == -1:
                    section = active_section or _find_section_for_episode(
                        catalog, item
                    )
                    if section is None:
                        code = 0
                        break
                    ep_picked = pick_episode(
                        section,
                        force_interactive=True,
                        watch_history=watch_history,
                    )
                    if ep_picked is None:
                        code = 0
                        break
                    ep_action, item = ep_picked
                    active_section = section
                    if ep_action == "ctrl-o":
                        if copy_magnet(item.entry.magnet):
                            print_status("magnet copied", kind="ok")
                        else:
                            print_status("clipboard unavailable", kind="warn")
                        continue
                    continue

                if is_play_completed(code):
                    section = active_section or _find_section_for_episode(
                        catalog, item
                    )
                    # Dernier épisode joué dans cette session mpv (chaîne binge).
                    last = (
                        binge_chain[-1]
                        if section is not None and binge_chain
                        else item
                    )
                    item = last
                    active_section = section
                    next_item = (
                        _next_episode_item(section, last)
                        if section is not None
                        else None
                    )
                    if next_item is not None:
                        nxt_ep = next_item.parsed.episode
                        nxt_s = next_item.parsed.season or (
                            section.season if section is not None else None
                        )
                        if nxt_s is not None and nxt_ep is not None:
                            print_status(
                                f"→ S{nxt_s:02d}E{nxt_ep:02d}", kind="info"
                            )
                        item = next_item
                        continue
                    break

                if code == PLAY_INCOMPLETE:
                    break

                break

            if item.parsed.season is not None:
                binge_season = item.parsed.season

            if is_user_cancel(code):
                break

            if code not in (PLAY_COMPLETED, PLAY_INCOMPLETE) and code != 0:
                print_status("playback interrupted", kind="warn")

            # Saison finie → liste des saisons. Quitter mpv tôt → re-pick épisode.
            if is_play_completed(code):
                require_episode_pick = False
                binge_season = None
            else:
                require_episode_pick = True


def _add_player_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--player",
        choices=["auto", "mpv", "vlc", "ffplay"],
        default="auto",
        help="Player (auto, or ANNIE_PLAYER)",
    )


def _add_target_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-s", "--season", type=int, help="Season")
    parser.add_argument("-e", "--episode", type=int, help="Episode")
    parser.add_argument("--movie", action="store_true", help="Movie")
    parser.add_argument("--ova", action="store_true", help="OVA")
    parser.add_argument("--special", action="store_true", help="Special")
    parser.add_argument("--batch", action="store_true", help="Batch")


def _add_nyaa_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--category", default=None, help="Nyaa category")
    parser.add_argument(
        "-f", "--filter", default=None, dest="filter", help="Nyaa filter"
    )


def main() -> int:
    try:
        return _main_impl()
    except KeyboardInterrupt:
        return EXIT_CANCELLED


def _main_impl() -> int:
    config = AnnieConfig.load()

    if len(sys.argv) == 1:
        return interactive_loop(config)

    parser = argparse.ArgumentParser(
        prog="annie",
        description="Nyaa · sort · stream torrents.",
    )
    parser.add_argument("--no-banner", action="store_true")
    sub = parser.add_subparsers(dest="command")

    search_cmd = sub.add_parser("search", help="Search Nyaa")
    search_cmd.add_argument("query")
    _add_target_flags(search_cmd)
    _add_nyaa_flags(search_cmd)
    search_cmd.add_argument("-l", "--limit", type=int, default=None)

    watch_cmd = sub.add_parser("watch", help="Search and stream")
    watch_cmd.add_argument("query")
    _add_target_flags(watch_cmd)
    _add_nyaa_flags(watch_cmd)
    watch_cmd.add_argument("-n", "--index", type=int)
    watch_cmd.add_argument("-q", "--query-file")
    watch_cmd.add_argument("--keep", action="store_true")
    _add_player_flag(watch_cmd)
    watch_cmd.add_argument(
        "--sub-lang",
        metavar="CODE",
        help="Subtitle language (en, zh, hi, es, fr)",
    )

    sub.add_parser("ls", help="List torrent files").add_argument("source")
    play_cmd = sub.add_parser("play", help="Stream a magnet or torrent")
    play_cmd.add_argument("source")
    play_cmd.add_argument("-n", "--index", type=int)
    play_cmd.add_argument("-q", "--query")
    play_cmd.add_argument("--keep", action="store_true")
    _add_player_flag(play_cmd)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if config.ui.show_banner and not args.no_banner:
        print_banner()

    if args.command == "search":
        return run_search(
            args.query,
            config,
            season=args.season,
            episode=args.episode,
            movie=args.movie,
            ova=args.ova,
            special=args.special,
            limit=args.limit,
            category=args.category,
            filter_code=args.filter,
        )
    if args.command == "watch":
        if (
            args.season is None
            and args.episode is None
            and not any([args.movie, args.ova, args.special, args.batch])
        ):
            print("  usage: watch <anime> -s 2 -e 5", file=sys.stderr)
            return 1
        return run_watch(
            args.query,
            config,
            season=args.season,
            episode=args.episode,
            movie=args.movie,
            ova=args.ova,
            special=args.special,
            batch=args.batch,
            index=args.index,
            query_file=args.query_file,
            keep=args.keep,
            category=args.category,
            filter_code=args.filter,
            player=None if args.player == "auto" else args.player,
            subtitle_lang=args.sub_lang,
        )
    if args.command == "ls":
        from annie.stream import list_files

        return list_files(args.source)
    from annie.stream import play

    return play(
        args.source,
        args.index,
        args.query,
        args.keep,
        player=config.resolved_player(None if args.player == "auto" else args.player),
        seed_while_watching=AnnieSettings.load().seed_while_watching,
    )
