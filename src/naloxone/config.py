"""
config.py: Configuration values. Secrets to be handled with Secrets Manager
"""

import logging
import socket
import urllib.request
from urllib.error import URLError

SKID_NAME = "naloxone-skid"

try:
    url = "http://metadata.google.internal/computeMetadata/v1/project/project-id"
    req = urllib.request.Request(url)
    req.add_header("Metadata-Flavor", "Google")
    with urllib.request.urlopen(req, timeout=15) as response:
        project_id = response.read().decode()
        if not project_id:
            raise ValueError
        HOST_NAME = project_id
except (TimeoutError, URLError, ValueError):
    HOST_NAME = socket.gethostname()

AGOL_ORG = "https://utah.maps.arcgis.com"
SENDGRID_SETTINGS = {  #: Settings for SendGridHandler
    "from_address": "noreply@utah.gov",
    "to_addresses": "",
    "prefix": f"{SKID_NAME} on {HOST_NAME}: ",
}
LOG_LEVEL = logging.DEBUG
LOG_FILE_NAME = "log"

# FEATURE_LAYER_ITEMID = "5f323cf2a05a4537bc12c3ac1d92996d"
JOIN_COLUMN = ""
ATTACHMENT_LINK_COLUMN = ""
ATTACHMENT_PATH_COLUMN = ""
FIELDS = {
    "Opts_In_OUT",
    "Name",
    "Provides_FTS_XTS_Yes_No",
    "Type",
    "Address",
    "City",
    "Zipcode",
    "Phone",
    "URL",
    "Hours",
    "x",
    "y",
    "score",
    "match_address",
    "x",
    "y",
    "score",
    "match_ad"
}
