#!/usr/bin/env python
#############################################################################
#
# Copyright (C) 2018 Fabian Schweinfurth
# Contact: autoshift <at> derfabbi.de
#
# This file is part of autoshift
#
# autoshift is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# autoshift is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with autoshift.  If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################
from __future__ import print_function

import sys
from typing import Match, cast

from common import _L, DEBUG, DIRNAME, INFO
# from query import BL3
from query import Key, known_games, known_platforms
from shift import ShiftClient, Status

client: ShiftClient = None # type: ignore

LICENSE_TEXT = """\
========================================================================
autoshift  Copyright (C) 2019  Fabian Schweinfurth
This program comes with ABSOLUTELY NO WARRANTY; for details see LICENSE.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
========================================================================
"""


def redeem(key: Key):
    import query
    """Redeem key and set as redeemed if successfull"""

    _L.info(f"Trying to redeem {key.reward} ({key.code}) on {key.platform}")
    status = client.redeem(key.code, known_games[key.game], key.platform)
    _L.debug(f"Status: {status}")

    # set redeemed status
    if status in (Status.SUCCESS, Status.REDEEMED,
                  Status.EXPIRED, Status.INVALID):
        query.db.set_redeemed(key)

    # notify user
    try:
        # this may fail if there are other `{<something>}` in the string..
        _L.info("  " + status.msg.format(**locals()))
    except:
        _L.info("  " + status.msg)

    return status == Status.SUCCESS


def query_keys(games: list[str], platforms: list[str]):
    """Query new keys for given games and platforms

    Returns dict of dicts of lists with [game][platform] as keys"""
    from itertools import groupby

    import query
    all_keys: dict[str, dict[str, list[Key]]] = {}

    keys = list(query.db.get_keys(None, None))
    # parse all keys
    query.update_keys()
    new_keys = list(query.db.get_keys(None, None))

    diff = len(new_keys) - len(keys)
    _L.info(f"done. ({diff if diff else 'no'} new Keys)")

    _g = lambda key: key.game
    _p = lambda key: key.platform
    for g, g_keys in groupby(sorted(new_keys, key=_g), _g):
        if g not in games:
            continue
        all_keys[g] = {p: [] for p in platforms}
        for platform, p_keys in groupby(sorted(g_keys, key=_p), _p):
            if platform not in platforms and platform != "universal":
                continue

            _ps = [platform]
            if platform == "universal":
                _ps = platforms.copy()

            #_L.debug(f"First Keys looks like: {all_keys}")
            # When universal, the key needs to be copied to each platform. temp_key is required to prevent iterator moving past the key before 
            # it's been copied for each platform
            for key in p_keys:
                temp_key=key
                for p in _ps:
                    _L.debug(f"Platform: {p}, {key}")
                    all_keys[g][p].append(temp_key.copy().set(platform=p))

            #_L.debug(f"All Keys looks like: {all_keys}")
        for p in platforms:
            # count the new keys
            n_golden = sum(int(cast(Match[str], m).group(1) or 1)
                            for m in
                            filter(lambda m:
                                    m  and m.group(1) is not None,
                                    map(lambda key: query.r_golden_keys.match(key.reward),
                                        all_keys[g][p])))

            _L.info(f"You have {n_golden} golden {g.upper()} keys to redeem for {p.upper()}")

    return all_keys


def setup_argparser():
    import argparse
    import textwrap
    games = list(known_games.keys())
    platforms = list(known_platforms.without("universal").keys())

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-u", "--user",
                        default=None,
                        help=("User login you want to use "
                              "(optional. You will be prompted to enter your "
                              " credentials if you didn't specify them here)"))
    parser.add_argument("-p", "--pass",
                        help=("Password for your login. "
                              "(optional. You will be prompted to enter your "
                              " credentials if you didn't specify them here)"))
    parser.add_argument("--golden",
                        action="store_true",
                        help="Only redeem golden keys")
    parser.add_argument("--non-golden", dest="non_golden",
                        action="store_true",
                        help="Only redeem non-golden keys")
    parser.add_argument("--games",
                        type=str, required=True,
                        choices=games, nargs="+",
                        help=("Games you want to query SHiFT keys for"))
    parser.add_argument("--platforms",
                        type=str, required=True,
                        choices=platforms, nargs="+",
                        help=("Platforms you want to query SHiFT keys for"))
    parser.add_argument("--limit",
                        type=int, default=200,
                        help=textwrap.dedent("""\
                        Max number of golden Keys you want to redeem.
                        (default 200)
                        NOTE: You can only have 255 keys at any given time!""")) # noqa
    parser.add_argument("--schedule",
                        type=float, const=2, nargs="?",
                        help="Keep checking for keys and redeeming every hour")
    parser.add_argument("-v", dest="verbose",
                        action="store_true",
                        help="Verbose mode")

    return parser


def main(args):
    global client
    from time import sleep

    import query
    from query import db, r_golden_keys

    with db:
        if not client:
            client = ShiftClient(args.user, args.pw)

        # query all keys
        all_keys = query_keys(args.games, args.platforms)

        # redeem 0 golden keys but only golden??... duh
        if not args.limit and args.golden:
            _L.info("Not redeeming anything ...")
            return

        _L.info("Trying to redeem now.")

        # now redeem
        for game in all_keys.keys():
            for platform in all_keys[game].keys():
                _L.info(f"Redeeming for {game} on {platform}")
                t_keys = list(filter(lambda key: not key.redeemed, all_keys[game][platform]))
                _L.info(f"Keys to be redeemed: {t_keys}")
                for num, key in enumerate(t_keys):

                    if (num and not (num % 15)) or client.last_status == Status.SLOWDOWN:
                        if client.last_status == Status.SLOWDOWN:
                            _L.info("Slowing down a bit..")
                        else:
                            _L.info("Trying to prevent a 'too many requests'-block.")
                        sleep(60)

                    _L.info(f"Key #{num+1}/{len(t_keys)} for {game} on {platform}")
                    num_g_keys = 0  # number of golden keys in this code
                    m = r_golden_keys.match(key.reward)

                    # skip keys we don't want
                    if ((args.golden and not m) or (args.non_golden and m)):
                        _L.debug("Skipping key not wanted")
                        continue

                    if m:
                        num_g_keys = int(m.group(1) or 1)
                        # skip golden keys if we reached the limit
                        if args.limit <= 0:
                            _L.debug("Skipping key as we've reached a limit")
                            continue

                        # skip if this code has too many golden keys
                        if (args.limit - num_g_keys) < 0:
                            _L.debug("Skipping key that has too many golden keys")
                            continue

                    redeemed = redeem(key)
                    if redeemed:
                        args.limit -= num_g_keys
                        _L.info(f"Redeeming another {args.limit} Keys")
                    else:
                        # don't spam if we reached the hourly limit
                        if client.last_status == Status.TRYLATER:
                            return

        _L.info("No more keys left!")


if __name__ == "__main__":
    import os

    # only print license text on first use
    if not os.path.exists(os.path.join(DIRNAME, "data", ".cookies.save")):
        print(LICENSE_TEXT)

    # build argument parser
    parser = setup_argparser()
    args = parser.parse_args()

    args.pw = getattr(args, "pass")

    _L.setLevel(INFO)
    if args.verbose:
        _L.setLevel(DEBUG)
        _L.debug("Debug mode on")

    if args.schedule and args.schedule < 2:
        _L.warn(f"Running this tool every {args.schedule} hours would result in "
                "too many requests.\n"
                "Scheduling changed to run every 2 hours!")

    # always execute at least once
    main(args)

    # scheduling will start after first trigger (so in an hour..)
    if args.schedule:
        hours = int(args.schedule)
        minutes = int((args.schedule-hours)*60+1e-5)
        _L.info(f"Scheduling to run every {hours:02}:{minutes:02} hours")
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler()
        # fire every 1h5m (to prevent being blocked by the shift platform.)
        #  (5min safe margin because it somtimes fires a few seconds too early)
        scheduler.add_job(main, "interval", args=(args,), hours=args.schedule)
        print(f"Press Ctrl+{'Break' if os.name == 'nt' else 'C'} to exit")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            pass
    _L.info("Goodbye.")
