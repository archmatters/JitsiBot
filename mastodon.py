#!/usr/bin/env python3

import datetime
import json
import logging
import os
import re
import string
import time
import traceback

import requests

import config

logger = logging.getLogger(__name__)

class Proboscis:
    """ An instance-specific, account-specific Mastodon interface, gradually being
        implemented as I need features.
    """
    application_name = "Proboscis"
    mastodon_instance = ""
    mastodon_token = ""
    account_id = None

    api_rate_remain = 300
    api_last_reset = datetime.datetime.now()
    api_next_reset = datetime.datetime.fromtimestamp(time.time() + 300)
    api_last_periods = []

    link_rel_pattern = re.compile('<(.*?)>;\\s*rel="([^"]*)"')
    only_alphanum_pattern = re.compile('[\\d\\w]')
    iso8601_pattern = re.compile('(\\d{4})-(\\d\\d)-(\\d\\d)T(\\d\\d):(\\d\\d):(\\d\\d)(\\.\\d+|)(.*)')

    def __init__( self, mastodon_instance: str, mastodon_token="", application_name="Proboscis",
            reset_period=300 ):
        """ The one and only Proboscis constructor.

            mastodon_instance: the base URI for the Mastodon instance you want to work with.
            mastodon_token: the user token for the account this Proboscis should use.
            application_name: unused for now.
            reset_period: the last observed API rate limit reset period for the instance;
                    use getObservedAPIResetPeriod() to retrieve, if you wish to persist this.
        """
        if not mastodon_instance:
            raise Exception("Proboscis requires a mastodon_instance")
        self.mastodon_instance = mastodon_instance
        self.mastodon_token = mastodon_token
        self.application_name = application_name
        if (reset_period > 0):
            self.api_last_periods = [ reset_period, reset_period, reset_period ]


    def getRateRemaining( self ):
        """ Mastodon API is rate-limited; you are allowed only a certain number of requests within a given window.
        This method returns the last known remaining count allowed for the current window.
        """
        return self.api_rate_remain


    def getEstimatedTimeToReset( self ):
        """ Based on observation, seconds remaining until the next API rate limit reset.
        """
        return int(time.time() - self.api_last_reset.timestamp() + self.getObservedAPIResetPeriod())


    def getObservedAPIResetPeriod( self ):
        """ The mean observed period in the API rate limit reset window.
            
            Why do we do this?  At least some instances lie about the rate limit window.
            The X-RateLimit-Reset header will indicate a time, and once that time arrives,
            the rate will not always reset. For example, X-RateLimit-Reset indicates that
            the limit should reset every five minutes, but in practice it actually resets
            every fifteen.

            If we have not observed any rate limit resets, we assume the default, 300 seconds.
        """
        est_period = 300
        if len(self.api_last_periods) > 2:
            est_period = 0
            for x in range(len(self.api_last_periods)):
                est_period += self.api_last_periods[x]
            est_period /= len(self.api_last_periods)
        return est_period


    def getEstimatedRateReset( self ):
        """ Based on observation, get the estmated time of the next rate limit reset.
        """
        return datetime.datetime.fromtimestamp(time.time() + self.getEstimatedTimeToReset())


    # check response status and rate limits
    # Returns True when the status is 200, otherwise False
    def checkResponse( self, response, caller=None, action=None ):
        """ Check the HTTP response from a Mastodon API call.  This function checks for
            failure, and updates API rate limit and reset information.  The content of
            the response is not inspected in any way.

            When the response is None, an exception is raised.  When the HTTP code is
            not 20x, the return value is False.  Otherwise the return value is True.
        """
        now = time.time()
        if not caller:
            stack = traceback.extract_stack(None, 2)
            for f in stack:
                caller = f.name
                break
        if action is None or not action.strip():
            action = ""
        else:
            action = f" from {action.strip()}"

        if not response:
            raise Exception(f"{caller}(): no response provided{action}")
        elif response.status_code < 200 or response.status_code > 299:
            raise Exception(f"{caller}(): HTTP status{action} = {response.status_code}")

        try:
            reset = ""
            limit = response.headers.get("X-RateLimit-Limit")
            sremain = response.headers.get("X-RateLimit-Remaining")
            reset_utc = response.headers.get("X-RateLimit-Reset")
            remain = int(sremain)
            if remain:
                if remain > self.api_rate_remain:
                    self.api_last_periods.append(time.time() - self.api_last_reset.timestamp())
                    while len(self.api_last_periods) > 10:
                        self.api_last_periods.remove(self.api_last_periods[0])
                    # assuming first two are bad
                    if len(self.api_last_periods) == 2:
                        self.api_last_periods[0] = self.api_last_periods[1]
                    self.api_last_reset = datetime.datetime.now()
                self.api_rate_remain = remain
            if reset_utc:
                try:
                    dtm = self.iso8601_pattern.match(reset_utc)
                    if dtm and "Z" == dtm.group(8):
                        self.api_next_reset = datetime.datetime.fromisoformat(reset_utc.replace("Z", "+00:00"))
                    elif dtm:
                        self.api_next_reset = datetime.datetime.fromisoformat(reset_utc)
                    if self.api_next_reset:
                        reset = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.api_next_reset.timestamp()))
                except Exception as e:
                    logger.error(f"checkResponse(): error reading time '{reset_utc}':")
                    logger.error(e)
                    self.api_next_reset = None
                    reset = reset_utc
            if int(remain) < 150:
                logger.warn(f"{caller}():{action} rate limit is {limit}; remaining {remain}; reset at {reset}")
            elif limit or remain or reset:
                est = self.getEstimatedTimeToReset()
                dttm = datetime.datetime.fromtimestamp(time.time() + est)
                logger.debug(f"{caller}():{action} rate limit is {limit}; remaining {remain}; reset at {reset} (est actual {est}sec={dttm})")
            return True
        except Exception as e:
            logger.error(f"{caller}(): error in response{action}:")
            logger.error(e)

        return False


    def _verifyCredentials( self ):
        headers = {}
        headers["Authorization"] = f"Bearer {self.mastodon_token}"
        response = requests.get(url=f"{self.mastodon_instance}/api/v1/accounts/verify_credentials", headers=headers)
        if not self.checkResponse(response, 'getAllFollowers', 'verify_credentials'):
            return None
        return response.json()


    def getAccountId( self ):
        """ Return the account ID for the mastodon_token passed to the constructor.
        """
        if not self.account_id:
            verify = self._verifyCredentials()
            if not verify:
                raise Exception("no valid response from verify_credentials")
            self.account_id = verify.get("id")
            if not self.account_id:
                raise Exception("no account ID obtained from verify_credentials")
        return self.account_id
    

    def getNotifications( self, since=None, limit=None ):
        """ Get the notification list for the account.
        """
        headers = {}
        headers["Authorization"] = f"Bearer {self.mastodon_token}"

        # if there is no known previous notification ID, just get the most recent
        params = {}
        if limit:
            params["limit"] = limit
        if since:
            params["since_id"] = since
        # max_id string Return results older than this ID
        # since_id string Return results newer than this ID
        # min_id string Return results immediately newer than this ID
        # limit string Maximum number of results to return (default 20)
        # exclude_types array Array of types to exclude (follow, favourite, reblog, mention, poll, follow_request)
        # account_id string Return only notifications received from this account

        response = requests.get(
            url=f"{self.mastodon_instance}/api/v1/notifications",
            params=params,
            headers=headers
        )

        if not self.checkResponse(response, 'getNotifications'):
            return []
        
        # it looks weird, but in debug I always want to see the count
        try:
            notes = response.json()
            if len(notes) > 0:
                logger.info(f"getNotifications(): got {len(notes)} notifications")
            elif notes is not None:
                logger.debug(f"getNotifications(): got {len(notes)} notifications")
            return notes
        except Exception as e:
            logger.error(f"getNotifications(): error parsing notifications response:")
            logger.error(e)
        
        return []
    

    # post a status
    def postStatus( self, content: str, reply_to_status_id=None ):
        """ Toot out a new post.  If reply_to_status_id is provided, the toot will be
            in reply to the provided status ID.  This returns True if the post succeeded,
            or False if there was a problem.
        """
        if not content or not content.strip():
            raise Exception("content is empty")

        idempotency = ""
        if reply_to_status_id and reply_to_status_id.strip():
            idempotency = self.application_name + '.Reply.' + reply_to_status_id + '.'
        else:
            idempotency = self.application_name + '.Toot.'
        for c in re.finditer('[\w\d]', content):
            idempotency += c.group(0)


        headers = {}
        headers["Authorization"] = f"Bearer {self.mastodon_token}"
        headers["Idempotency-Key"] = idempotency

        data = {}
        data["status"] = content
        if reply_to_status_id:
            data["in_reply_to_id"] = f"{reply_to_status_id}"
        data["visibility"] = "public" # not "direct"

        response = requests.post(
            url=f"{self.mastodon_instance}/api/v1/statuses",
            headers=headers,
            data=data
        )

        return self.checkResponse(response, 'postStatus')
            
    
    # Returns a simple list of account names
    def getAllFollowers( self, account_id ):
        if not account_id:
            raise Exception("no account_id provided for followers")

        nlist = []
        headers = {}
        if self.mastodon_token:
            headers["Authorization"] = f"Bearer {self.mastodon_token}"
        follow_url = f"{self.mastodon_instance}/api/v1/accounts/{account_id}/followers"
        # Query Parameters
        # max_id string
        # since_id string
        # limit number

        while follow_url:
            response = requests.get(url=follow_url, headers=headers)
            if not self.checkResponse(response, 'getAllFollowers', 'followers'):
                raise Exception("could not get all followers")

            flist = None
            try:
                flist = response.json()
            except Exception as e:
                logger.error("getAllFollowers(): error parsing response as JSON:")
                logger.error(response.text)
                raise Exception("bad JSON") from e
        
            if flist:
                for follower in flist:
                    if follower and follower.get("acct"):
                        nlist.append(follower.get("acct"))
            
            follow_url = None
            links = response.headers.get("Link")
            if links:
                pos = 0
                lm = None
                rel = "X"
                while rel != "next":
                    lm = self.link_rel_pattern.search(links, pos)
                    if lm:
                        rel = lm.group(2)
                        pos = lm.endpos
                    else:
                        rel = "next"
                
                if lm:
                    follow_url = lm.group(1)
        
        return nlist


    def getStatus( self, id: str ):
        if not id:
            return
        
        response = requests.get(url=f"{self.mastodon_instance}/api/v1/statuses/{id}")
        if not self.checkResponse(response, 'getStatus'):
            return None

        return response.json()

