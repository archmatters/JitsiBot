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
    """ Application logic.  Polls for Mastodon notifications, and will respond
        to new follows and mentions with the code phrase or a recognized variation.
    """

    # We will not "sound the horn" more than once within the "horn window."
    # This is in seconds.
    horn_window = 1800
    # The notification polling period.
    note_poll_period = 15

    trunk = None
    jitsi_link = None
    storage_file = None
    account_id = None
    last_note_id = ""
    last_horn_time = 0
    api_reset_period = 900

    horn_pattern = re.compile('\\b(?:toot|sound|blow)(?:\\s+on|)\\s+(?:teh|the|that|your?)\\s+horn\\b', re.IGNORECASE)

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
                self.api_reset_period = store.get("api_reset_period", 900)
            except Exception as e:
                logger.error(f"_readStore(): failed to read state from {self.storage_file}")
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
            logger.error(f"_writeStore(): failed to persist state to {self.storage_file}")
            logger.error(e)
        finally:
            if stream:
                stream.close()


    def doTheWork( self ):
        # change errorLimit to change the maximum tolerable sequential errors
        connectErrors = 0
        totalTime = 0
        if not self.last_note_id:
            # instead of using whatever the mastodon instance default is, let's
            # get the last one and start from that limit.
            notes = self.trunk.getNotifications(limit=1)
            if (len(notes) > 0):
                self.last_note_id = notes[0].get('id')
                self._writeStore()
        while True:
            # you gotta do the work
            try:
                self.processNotes()
                if connectErrors != 0:
                    logger.info(f"doTheWork(): connect successful after {timeToText(totalTime)} waiting and {connectErrors} errors")
                    connectErrors = 0
                    totalTime = 0
                time.sleep(self.note_poll_period)
            except requests.ConnectionError as e:
                connectErrors += 1
                delay = min(32 * 60, self.note_poll_period * 2 ** connectErrors)
                totalTime += delay
                logger.error(e)
                logger.error(f"doTheWork(): connection error #{connectErrors}, waiting {timeToText(delay)} (will be {timeToText(totalTime)} total)")
                time.sleep(delay)
    

    def processNotes( self ):
        notes = self.trunk.getNotifications(self.last_note_id)

        # switch to different follow messages based on whether we may be tooting or not
        new_followers = []
        all_requestors = {}
        final_id = None
        for note in reversed(notes):
            ntype = note.get('type') # mention, follow, favourite, reblog, poll, follow_request
            id = note.get('id') # str
            account = note.get('account') # obj
            status = note.get('status') # obj
            logger.info(f"processNotes(): notification id={id} type={ntype}")
            final_id = id
            if ntype == 'follow' and id and account:
                logger.info(f"New follower @{account.get('acct')}")
                new_followers.append(account.get('acct'))
            elif ntype == 'mention' and id and account and status:
                # account.username is local name, account.acct might be fqun
                nfrom = account.get('acct')
                status_id = status.get('id')
                content = status.get('content')
                if nfrom and status_id and content and self.horn_pattern.search(content):
                    # ignore if older than six hours
                    age = 0
                    created = status.get('created_at')
                    if created:
                        dtm = mastodon.parseISODate(created)
                        if dtm:
                            age = time.time() - dtm.timestamp()
                    if age > 6 * 3600:
                        logger.warn('processNotes(): ignoring horn request status='
                                + f"{status_id} due to age ({timeToText(time.time() - dtm.timestamp())})")
                    else:
                        logger.info(f"processNotes(): status={status_id} got a request to sound the horn from {nfrom}!")
                        all_requestors[nfrom] = status_id
                else:
                    logger.info("No horn request found in '%s' from %s", content, nfrom)
        
        if len(new_followers) == 0 and len(all_requestors) == 0:
            if final_id and self.last_note_id != final_id:
                self.last_note_id = final_id
                self._writeStore()
            return

        recent_horn = False
        time_since_horn = time.time() - self.last_horn_time
        if time_since_horn < self.horn_window:
            recent_horn = True
            if len(all_requestors) > 0:
                logger.warn(f"processNotes(): I refuse to toot again after only {timeToText(time_since_horn)} ({time_since_horn} sec)")

        if recent_horn or len(all_requestors) > 0:
            follow_message = f"Jitsi may be going right now: {self.jitsi_link}\nAnd I'll let you the next time when someone tells me to toot the horn!"
        else:
            follow_message = f"I'll let you know when someone tells me to toot the horn!"

        for follower in new_followers:
            self.trunk.postStatus(f"Hello @{follower}, " + follow_message)

        if len(all_requestors) > 0 and not recent_horn:
            self.tootThatHorn(all_requestors, new_followers)
        # TODO else reply saying we're in the no-notify window

        # update last ID storage if there was some update
        if len(notes) > 0 and final_id:
            self.last_note_id = final_id
            self._writeStore()
    

    def tootThatHorn( self, requestors: dict, skip_followers: list ):
        followers = self.trunk.getAllFollowers(self.trunk.getAccountId())
        for name in requestors:
            try:
                followers.remove(name)
            except Exception as e:
                logger.info(f"tootThatHorn(): did not remove requestor '{name}' from followers: {e}")
        for name in skip_followers:
            try:
                followers.remove(name)
            except Exception as e:
                logger.info(f"tootThatHorn(): did not remove follower '{name}' from followers: {e}")

        logger.info(f"tootThatHorn(): tooting to {len(followers)} followers")

        # check remaining API calls in remaining window, divide by followers per toot,
        # and loop period, figuring out if we should rate-limit ourselves
        calls_remain = self.trunk.getRateRemaining()
        time_remain = self.trunk.getEstimatedTimeToReset()
        # account for notification polling
        calls_remain -= int(time_remain / self.note_poll_period) + len(requestors)
        logger.info(f"tootThatHorn(): {calls_remain} calls left after polling in {time_remain} secs")
        if calls_remain < 5:
            per_toot = 10
            wait_between = self.note_poll_period * 2
        else:
            per_toot = 2
            toots_needed = calls_remain + 1
            while toots_needed > calls_remain and per_toot < 10:
                per_toot += 1
                toots_needed = len(followers) / per_toot

        wait_between = 0
        if toots_needed > calls_remain:
            wait_between = time_remain / toots_needed + 1

        if wait_between > 0:
            logger.info(f"tootThatHorn(): tooting to {len(followers)} followers {per_toot} at a time waiting {wait_between} secs")
        else:
            logger.info(f"tootThatHorn(): tooting to {len(followers)} followers {per_toot} at a time")

        pos = 0
        while pos < len(followers):
            # three at a time?
            toot = ""
            for xa in range(pos, min(len(followers), pos + per_toot)):
                if len(toot) > 0:
                    toot += ' '
                toot += '@'
                toot += followers[xa]
            toot += "\nHear ye, hear ye, Jitsi is in session: "
            toot += self.jitsi_link
            while not self.trunk.postStatus(toot):
                reset = self.trunk.getEstimatedTimeToReset()
                # estimated reset may be early; if so we'll get another failure and
                # we need to ensure we wait long enough for the actual reset
                if reset < self.note_poll_period:
                    reset = self.note_poll_period
                logger.warn(f"tootThatHorn(): failed to toot while sounding the horn; waiting {reset} sec for next reset.")
                time.sleep(reset)
            # now that we've successfully tooted again
            pos += per_toot
            time.sleep(wait_between)

        self.last_horn_time = time.time()
        self._writeStore()
        # TODO we could batch these also
        for name in requestors:
            self.trunk.postStatus(f"@{name} Job's done! Toot toot!\n{self.jitsi_link}", requestors[name])


def timeToText( seconds: int ):
    """ Returns an abbreviated text representation of a time period:
        '59 sec' '59 min' '1 hr' '1 hr 12 min' '1 day' '2 days 8 hr'
    """
    if seconds >= 86400:
        hrs = (seconds % 86400) / 3600
        days = int(seconds / 86400)
        if days == 1:
            word = "day"
        else:
            word = "days"
        if seconds < 6 * 86400 and hrs > 1: # only report hours up to six days
            return f"{days} {word} {int(hrs)} hr"
        else:
            return f"{days} {word}"
    elif seconds >= 3600:
        mins = (seconds % 3600) / 60
        #if abs(mins - 30) < 20: # for rounding
        if seconds < 6 * 3600 and mins > 10: # only report minutes up to six hours
            return f"{int(seconds / 3600)} hr {int(mins)} min"
        else:
            return f"{int(seconds / 3600)} hr"
    elif seconds >= 60:
        return f"{int(seconds / 60)} min"
    else:
        return f"{int(seconds)} sec"


