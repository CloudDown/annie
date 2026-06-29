"""CLI Annie."""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from annie.settings import AnnieSettings
from annie.media import (
    AnnieConfig,
    MediaKind,
    MediaSection,
    ResultItem,
    WatchTarget,
    build_catalog,
    build_catalog_from_releases,
    fill_catalog_gaps,
    fill_section_gaps,
    minimal_label,
    pick_best,
    rank_entry,
    resolve_catalog_target,
    scope_releases_for_target,
)
from annie.mal import (
    collect_franchise,
    franchise_to_releases,
    pick_candidate,
    relation_nyaa_hints,
    search_anime,
)
from annie.nyaa import search
from annie.ui import (
    BackToEpisode,
    BACK_TO_EPISODE,
    C,
    copy_magnet,
    fzf_available,
    pick_catalog,
    pick_episode,
    pick_subtitle_language,
    read_query,
    run_search_spinner,
    print_banner,
    print_help,
    print_status,
    stylize,
)

MOVIE_NUMBER_RE = re.compile(r"\bmovie\s*(?P<num>[1-9])\b", re.I)


def parse_inline_target(query: str) -> tuple[str, dict]:
    lowered = query.strip()
    options = {
        "season": None,
        "episode": None,
        "movie": False,
        "movie_number": None,
        "ova": False,
        "special": False,
        "batch": False,
        "direct": False,
    }

    match = re.search(r"\bs(?P<season>\d{1,2})e(?P<episode>\d{1,3})\b", lowered, re.I)
    if match:
        base = lowered[: match.start()].strip()
        options.update(
            season=int(match.group("season")),
            episode=int(match.group("episode")),
            direct=True,
        )
        return base, options

    match = re.search(r"\b(?P<season>\d{1,2})\s+(?P<episode>\d{1,3})\s*$", lowered)
    if match:
        base = lowered[: match.start()].strip()
        options.update(
            season=int(match.group("season")),
            episode=int(match.group("episode")),
            direct=True,
        )
        return base, options

    match = re.search(r"\b(?:movie|film|m)\s*(?P<num>[1-9])\b", lowered, re.I)
    if match:
        base = re.sub(r"\b(?:movie|film|m)\s*[1-9]\b", "", lowered, flags=re.I).strip()
        options.update(movie=True, movie_number=int(match.group("num")), direct=True)
        return base, options

    match = re.search(r"\bs(?P<season>\d{1,2})\b", lowered, re.I)
    if match:
        base = lowered[: match.start()].strip()
        options["season"] = int(match.group("season"))
        return base, options

    if re.search(r"\b(?:movie|film)\b", lowered, re.I):
        base = re.sub(r"\b(?:movie|film)\b", "", lowered, flags=re.I).strip()
        options["movie"] = True
        return base, options

    if re.search(r"\bova\b", lowered, re.I):
        base = re.sub(r"\bova\b", "", lowered, flags=re.I).strip()
        options["ova"] = True
        return base, options

    return lowered, options


def kind_from_options(options: dict) -> MediaKind | None:
    if options.get("movie"):
        return MediaKind.MOVIE
    if options.get("ova"):
        return MediaKind.OVA
    if options.get("special"):
        return MediaKind.SPECIAL
    if options.get("batch"):
        return MediaKind.BATCH
    return None


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


def _warm_nyaa(query: str, *, category: str, filter_code: str) -> None:
    if not query:
        return
    try:
        search(query, category=category, filter_code=filter_code)
    except Exception:
        pass


def gather_catalog(
    raw_query: str, config: AnnieConfig, **overrides
) -> tuple[list, dict]:
    query, options = parse_inline_target(raw_query)
    category = overrides.get("category") or config.category
    filter_code = overrides.get("filter_code") or config.filter_code
    fill_gaps = overrides.get("fill_gaps", config.catalog.fill_gaps_on_search)
    target_season = overrides.get("target_season", options.get("season"))
    target_kind = overrides.get("target_kind", kind_from_options(options))

    if config.mal.enabled:
        try:
            with ThreadPoolExecutor(max_workers=config.ui.mal_pool_workers) as pool:
                candidates_future = pool.submit(search_anime, query)
                pool.submit(
                    _warm_nyaa, query, category=category, filter_code=filter_code
                )

                candidates = candidates_future.result()
                chosen = pick_candidate(candidates, query) if candidates else None
                if chosen is not None:
                    options["mal_titles"] = tuple(
                        title
                        for title in (
                            chosen.title_english,
                            chosen.title,
                            chosen.title_japanese,
                        )
                        if title
                    )
                    for warm_q in dict.fromkeys(
                        [
                            chosen.title_english or "",
                            chosen.title,
                            chosen.title_japanese or "",
                        ]
                    ):
                        if warm_q:
                            pool.submit(
                                _warm_nyaa,
                                warm_q,
                                category=category,
                                filter_code=filter_code,
                            )

                    def on_root(root_data: dict) -> None:
                        for hint in relation_nyaa_hints(root_data):
                            pool.submit(
                                _warm_nyaa,
                                hint,
                                category=category,
                                filter_code=filter_code,
                            )

                    franchise = pool.submit(
                        collect_franchise,
                        chosen.mal_id,
                        on_root=on_root,
                        pool=pool,
                    ).result()
                    releases = franchise_to_releases(
                        franchise,
                        skip_recap=config.skip_recap_movies,
                        root_id=chosen.mal_id,
                        user_query=query,
                    )
                    if releases:
                        releases = scope_releases_for_target(
                            releases,
                            season=target_season,
                            kind=target_kind,
                        )
                        catalog = build_catalog_from_releases(
                            releases,
                            search=search,
                            category=category,
                            filter_code=filter_code,
                            skip_recap_movies=config.skip_recap_movies,
                            pool=pool,
                            fill_gaps=False,
                        )
                        if catalog:
                            if fill_gaps:
                                fill_catalog_gaps(
                                    catalog,
                                    search=search,
                                    category=category,
                                    filter_code=filter_code,
                                    skip_recap_movies=config.skip_recap_movies,
                                    pool=pool,
                                )
                            return catalog, options
        except Exception as exc:
            print(
                stylize(
                    f"annie: catalogue MAL indisponible ({exc}), fallback Nyaa",
                    C.MUTED,
                ),
                file=sys.stderr,
                flush=True,
            )

    entries = search(query, category=category, filter_code=filter_code)
    if not entries:
        return [], options

    kind = kind_from_options(options)
    catalog = build_catalog(
        entries,
        query,
        season=options.get("season"),
        episode=options.get("episode"),
        kind=kind if options.get("season") or options.get("episode") or kind else None,
        skip_recap_movies=config.skip_recap_movies,
    )
    return catalog, options


def print_status_line(label: str, seeders: int, release_group: str | None) -> None:
    del seeders, release_group
    print(stylize(f"◆ {label}", C.YELLOW, C.BOLD), flush=True)


def _find_section_for_item(
    catalog: list[MediaSection], item: ResultItem
) -> MediaSection | None:
    for section in catalog:
        for candidate in section.choices():
            if candidate.entry.magnet == item.entry.magnet:
                return section
    return None


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
    if interactive and fzf_available():
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
    interactive_subs: bool = False,
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

    label = minimal_label(item.parsed)
    print_status_line(label, item.entry.seeders, item.parsed.release_group)
    from annie.stream import play

    return play(
        item.entry.magnet,
        None,
        file_query,
        keep,
        player=config.resolved_player(player),
        episode=episode,
        season=season,
        seed_while_watching=AnnieSettings.load().seed_while_watching,
        subtitle_lang=lang,
        subtitle_query=subtitle_query,
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
            item = resolve_catalog_target(
                catalog,
                season=season or catalog_options.get("season"),
                episode=episode or catalog_options.get("episode"),
                kind=kind_from_options(options),
                movie_number=movie_number,
            )
            if item is not None:
                return play_item(
                    item,
                    config,
                    keep=keep,
                    player=player,
                    series_query=raw_query,
                    mal_titles=tuple(catalog_options.get("mal_titles", ())),
                    interactive_subs=True,
                )
        except Exception:
            pass

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
            item = resolve_catalog_target(
                catalog,
                season=season,
                episode=episode,
                kind=kind_from_options(options),
            )
            if item is not None:
                return play_item(
                    item,
                    config,
                    keep=keep,
                    player=player,
                    subtitle_lang=subtitle_lang,
                    series_query=raw_query,
                    mal_titles=tuple(catalog_options.get("mal_titles", ())),
                )
        except Exception:
            pass

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
    print_status_line(label, entry.seeders, parsed.release_group)
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
    )


def interactive_loop(config: AnnieConfig) -> int:
    if not fzf_available():
        print_status("fzf not found — install with: pacman -S fzf", kind="err")
        return 1
    if not sys.stdout.isatty():
        print_status("interactive mode requires a TTY", kind="err")
        return 1

    if config.ui.show_banner:
        print_banner()

    while True:
        raw_query = read_query()
        if raw_query is None:
            return 0
        if not raw_query:
            continue

        lowered = raw_query.lower()
        if lowered in {"help", "?", "/help"}:
            print_help()
            continue
        if lowered in {"quit", "exit", "q"}:
            return 0

        direct = try_direct_play(raw_query, config)
        if direct is not None:
            if direct != 0:
                print_status("playback interrupted", kind="warn")
            continue

        try:
            query, options = parse_inline_target(raw_query)
            catalog, options = run_search_spinner(
                query,
                lambda: gather_catalog(
                    raw_query,
                    config,
                    target_season=options.get("season"),
                    target_kind=kind_from_options(options),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            print_status(str(exc), kind="err")
            continue

        if not catalog:
            print_status("no results", kind="warn")
            continue

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

        while True:
            picked = pick_catalog(
                catalog,
                season=binge_season,
                episode=next_episode,
                kind=kind_from_options(options),
                on_section=on_section,
            )
            next_episode = None

            if picked is None:
                break

            action, item = picked
            if action == "ctrl-o":
                if copy_magnet(item.entry.magnet):
                    print_status("magnet copied", kind="ok")
                else:
                    print_status("clipboard unavailable", kind="warn")
                continue

            code = 0
            while True:
                try:
                    code = play_item(
                        item,
                        config,
                        series_query=raw_query,
                        mal_titles=tuple(options.get("mal_titles", ())),
                        interactive_subs=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    print_status(str(exc), kind="err")
                    code = 1
                    break
                if code != -1:
                    break

                section = _find_section_for_item(catalog, item)
                if section is None:
                    code = 0
                    break
                ep_picked = pick_episode(section)
                if ep_picked is None:
                    code = 0
                    break
                ep_action, item = ep_picked
                if ep_action == "ctrl-o":
                    if copy_magnet(item.entry.magnet):
                        print_status("magnet copied", kind="ok")
                    else:
                        print_status("clipboard unavailable", kind="warn")
                    continue

            if item.parsed.season is not None:
                binge_season = item.parsed.season

            if code != 0:
                print_status("playback interrupted", kind="warn")


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
        help="Langue sous-titres (en, zh, hi, es, fr)",
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
