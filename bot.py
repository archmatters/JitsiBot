#!/usr/bin/env python3

import logging
import sys
import time

import config
import tootscanner
import mastodon
from requests import ConnectionError

if __name__ == "__main__":

    # Initialize variables
    cfg = config.Config("config.json")
    log_level_text = cfg.get("log_level")

    try:
        # Initialize common logging options
        logger = logging.getLogger(__name__)
        logging.basicConfig(
            level=log_level_text,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    except Exception as e:
        logger.error(f"__main__: error setting log level to '{log_level_text}'; using INFO")
        logger.error(e)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    # create the scanner job
    job = tootscanner.TootScanner(cfg=cfg)

    connectErrors = 0
    while True:
        # you gotta do the work
        try:
            job.doTheWork()
            connectErrors = 0
            time.sleep(15)
        except ConnectionError as e:
            connectErrors += 1
            logger.error(e)
            if connectErrors > 15:
                logger.fatal("__main__: after two hours (fifteen failures to connect), I give up.")
                exit(1)
            if connectErrors > 1:
                sfx = "s"
                wrd = "another "
            else:
                sfx = ""
                wrd = "a "
            logger.error(f"__main__: sleeping for {connectErrors} minute{sfx} after {wrd}connection error ({int((connectErrors*connectErrors+connectErrors)/2)} mins total)")
            time.sleep(60 * connectErrors)

