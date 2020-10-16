#!/usr/bin/env python3

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Used for reading a JSON configuration file
class Config:
    jsonMap = None

    # the class constructor reads the file into the map
    def __init__( self, filename: str ):
        configFile = Path(os.path.join(os.path.dirname(__file__), filename))
        
        if not configFile.exists:
            logger.critical(f"get(): cannot find configuration file '{filename}'")
            sys.exit(1)
        
        try:
            configFile.open(mode="r")
            text = configFile.read_text()
            self.jsonMap = json.loads(text)
        except Exception as e:
            logger.critical(f"get(): cannot read JSON from '{filename}'")
            logger.critical(e)

    # get the value for a configuration item
    def get( self, key: str ):
        if self.jsonMap is None:
            return None

        return self.jsonMap.get(key)
