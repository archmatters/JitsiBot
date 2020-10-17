#!/usr/bin/env python3

import json
import logging
import os
import re
import time

import requests
from pathlib import Path

import config
import mastodon

logger = logging.getLogger(__name__)

class TootScanner:
    """ We will not "sound the horn" more than once within the "horn window."
        This is in seconds.
    """
    horn_window = 1800
    """ The notification polling period. """
    note_poll_period = 15

    trunk = None
    jitsi_link = None
    storage_file = None
    account_id = None
    last_note_id = ""
    last_horn_time = 0
    api_reset_period = 0

    horn_pattern = re.compile('\\b(?:toot|sound|blow)(?:\\s+on)\\s+(?:teh|the|that|your?)\\s+horn\\b', re.IGNORECASE)

    def __init__( self, cfg: config.Config ):
        self.jitsi_link = cfg.get("jitsi_link")
        self.storage_file = os.path.join(cfg.get("storage_dir"), "JitsiBot00.storage")
        self._readStore()
        self.trunk = mastodon.Proboscis(
            mastodon_instance=cfg.get("mastodon_instance"),
            mastodon_token=cfg.get("mastodon_token"),
            application_name="Proboscis",
            reset_period=self.api_reset_period
        )


    def _readStore( self ):
        # if the notification storage file exists, read that value in
        if os.path.exists(self.storage_file):
            try:
                stream = Path(self.storage_file).open("r")
                store = json.load(stream)
                self.last_note_id = store.get("last_note_id")
                self.last_horn_time = store.get("last_horn_time", 0)
                self.api_reset_period = store.get("api_reset_period", 300)
            except Exception as e:
                logger.error(f"init(): failed to read from {self.storage_file}")
                logger.error(e)
            finally:
                if stream:
                    stream.close()

    
    def _writeStore( self ):
        store = {}
        store["last_note_id"] = self.last_note_id
        store["last_horn_time"] = self.last_horn_time
        store["api_reset_period"] = self.trunk.getObservedAPIResetPeriod()

        stream = None
        try:
            stream = Path(self.storage_file).open("w")
            stream.write(json.dumps(store))
        except Exception as e:
            logger.error(f"doTheWork(): failed to write notification ID to {self.storage_file}")
            logger.error(e)
        finally:
            if stream:
                stream.close()


    def doTheWork( self ):
        # change errorLimit to change the maximum tolerable sequential errors
        errorLimit = 15
        connectErrors = 0
        while True:
            # you gotta do the work
            try:
                self.processNotes()
                connectErrors = 0
                time.sleep(self.note_poll_period)
            except requests.ConnectionError as e:
                totalTime = int((connectErrors * connectErrors + connectErrors) / 2) # calculate before incrementing count
                connectErrors += 1
                logger.error(e)
                if connectErrors > 15:
                    logger.fatal(f"__main__: after {timeToText(totalTime)} ({connectErrors - 1} failures to connect), I give up.")
                    exit(1)
                if connectErrors == 1:
                    logger.error(f"__main__: sleeping for {connectErrors} minute after a connection error (will be {totalTime+connectErrors} minute total)")
                else:
                    logger.error(f"__main__: sleeping for {connectErrors} minutes after another connection error (will be {totalTime+connectErrors} mins total)")
                time.sleep(60 * connectErrors)
    

    def processNotes( self ):
        notes = self.trunk.getNotifications(self.last_note_id)

        # for each notification (in reverse order), verify we see all expected elements, then reply
        # scan for new followers first,
        for note in reversed(notes):
            ntype = note.get("type") # mention, follow, favourite, reblog, poll, follow_request
            id = note.get("id") # str
            account = note.get("account") # obj
            status = note.get("status") # obj
            logger.info(f"doTheWork(): notification id={id} type={ntype}")
            if id and account and ntype == "follow":
                logger.info(f"New follower @{account.get('acct')}")
                self.trunk.postStatus(f"Hello @{account.get('acct')}, I'll let you know when someone tells me to toot the horn!")
                # update the store after every new follow in case of fatal error
                self.last_note_id = id
                self._writeStore()
            self.last_note_id = id
        # then look for toots
        for note in reversed(notes):
            ntype = note.get("type") # mention, follow, favourite, reblog, poll, follow_request
            id = note.get("id") # str
            account = note.get("account") # obj
            status = note.get("status") # obj
            if id and account and status and ntype == 'mention':
                # account.username is local name, account.acct is fqun
                nfrom = account.get("acct")
                status_id = status.get("id")
                content = status.get("content")
                if nfrom and status_id and self.horn_pattern.search(content):
                    logger.info(f"doTheWork(): status={status_id} got a request to sound the horn!")
                    self.tootThatHorn(nfrom, status_id)
                    # TODO shouldn't we just drop out of the loop here?
                    # or should we process all at once, and drop all those from the followers?
                    # or... ?
            self.last_note_id = id

        # update last ID storage if there was some update
        if len(notes) > 0 and self.last_note_id:
            self._writeStore()
    

    def tootThatHorn( self, source_name: str, source_status_id: str ):
        timeSince = time.time() - self.last_horn_time
        if timeSince < self.horn_window:
            logger.warn(f"tootThatHorn(): I refuse to toot again after only {timeSince} seconds")
            return

        followers = self.trunk.getAllFollowers(self.trunk.getAccountId())
        try:
            followers.remove(source_name)
        except Exception as e:
            logger.error(f"tootThatHorn(): error removing '{source_name}' from followers")
            logger.error(e)

        # TODO we can check remaining API calls in remaining window, divide by followers per toot,
        # and loop period, figuring out if we should rate-limit ourselves
        calls_remain = trunk.getRateRemaining()
        time_remain = trunk.getObservedAPIResetPeriod()

        pos = 0
        logger.info(f"tootThatHorn(): tooting to {len(followers)} followers")
        while pos < len(followers):
            # three at a time?
            toot = ""
            for xa in range(pos, min(len(followers), pos + 3)):
                if len(toot) > 0:
                    toot += ' '
                toot += '@'
                toot += followers[xa]
            pos += 3
            toot += "\nHear ye, hear ye, Jitsi is in session: "
            toot += self.jitsi_link
            self.trunk.postStatus(toot)
            self.last_horn_time = time.time()
        
        self._writeStore()

        self.trunk.postStatus(f"@{source_name} Job's done! Toot toot!\n{self.jitsi_link}", source_status_id)


def timeToText( seconds: int ):
    """ Returns an abbreviated textual string representation of a time period:
        '59 sec' '59 min' '1 hr' '1 hr 12 min'
    """
    if seconds >= 3600:
        mins = (seconds % 3600) / 60
        #if abs(mins - 30) < 20: # for rounding
        if seconds < 14400 and mins > 10: # only report minutes up to four hours
            return f"{int(seconds / 3600)} hr {int(mins)} min"
        else:
            return f"{int(seconds / 3600)} hr"
    elif seconds >= 60:
        return f"{int(seconds / 60)} min"
    else:
        return f"{int(seconds)} sec"

