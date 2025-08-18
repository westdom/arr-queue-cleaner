# sonarr-radarr-queue-cleaner

A simple Sonarr, Radarr & Lidarr script to clean out stalled/slow downloads.
Couldn't find a python script to do this job so I figured why not give it a try.

Details:

This script checks every 10 minutes (configurable in seconds) Sonarr's, Radarr's & Lidarrs queue json information for downloads that has an `errorMessage` that states `The download is stalled with no connections` or if the download speed is less than the `DOWNLOAD_SPEED_CUTOFF` (kb/s) env var for each item in the queue. 

**NEW: Consecutive Hit System**: The script now uses a consecutive hit system to prevent false positives. A torrent will only be removed after it has been flagged for removal X consecutive times in a row (default: 3). This helps avoid removing torrents that are temporarily slow or stalled but recover quickly. The hit counter is stored in memory and resets if a torrent becomes healthy again or if the removal reason changes.

The script uses asyncio to allow each call to wait for a response before proceeding.
Logging defaults to the `INFO` level, but you can configure this to be e.g. `DEBUG` to get more information.

This script was created to work in a docker container so the included files are necessary.
to use in a docker container, copy folder to the machine hosting your docker, `CD` into the directory where the files are located and enter these following 2 commands:

1# `docker build -t media-cleaner .`

2#. `docker run -d --name media-cleaner -e SONARR_API_KEY='123456' -e RADARR_API_KEY='123456' -e SONARR_URL='http://sonarr:8989' -e RADARR_URL='http://radarr:7878' -e API_TIMEOUT='600' -e LOG_LEVEL='INFO' -e CONSECUTIVE_HITS_REQUIRED='3' media-cleaner`

## Environment Variables

- `CONSECUTIVE_HITS_REQUIRED`: Number of consecutive times a torrent must be flagged for removal before it's actually removed (default: 3)
- `DOWNLOAD_SPEED_CUTOFF`: Download speed threshold in kb/s below which torrents are flagged for removal
- `API_TIMEOUT`: How often to check the queues (in seconds)
- `LOG_LEVEL`: Logging level (INFO, DEBUG, etc.)
