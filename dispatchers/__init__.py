# -*- coding: utf-8 -*-
from .x_twitter import XTwitterDispatcher
from .deviantart import DeviantArtDispatcher
from .reddit import RedditDispatcher
from .monetization import PatreonDispatcher, KofiDispatcher

def get_all_dispatchers(cookies_dir):
    return [
        XTwitterDispatcher(cookies_dir),
        DeviantArtDispatcher(cookies_dir),
        RedditDispatcher(cookies_dir),
        PatreonDispatcher(cookies_dir),
        KofiDispatcher(cookies_dir)
    ]
