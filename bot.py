#!/usr/bin/env python3

import logging
import sys
import time

import config
import tootscanner
import mastodon

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

    # you gotta do the work
    job.doTheWork()

