import requests
import os
import makeRequest
import arrAPI
import logging
import re

QBITTORRENT_API_URL = (os.environ["QBITTORRENT_URL"]) + "/api/v2"
QBITTORRENT_USERNAME = os.environ["QBITTORRENT_USERNAME"]
QBITTORRENT_PASSWORD = os.environ["QBITTORRENT_PASSWORD"]

DOWNLOAD_SPEED_CUTOFF = os.environ["DOWNLOAD_SPEED_CUTOFF"]

# Global dictionary to track consecutive hits for each torrent hash
# Format: {torrent_hash: {"hits": count, "reason": reason}}
torrent_hit_counter = {}

# Number of consecutive hits required before removal
CONSECUTIVE_HITS_REQUIRED = int(os.environ.get("CONSECUTIVE_HITS_REQUIRED", "3"))


async def login_to_qbittorrent(session: requests):
    await makeRequest.make_request(
        session.post(
            QBITTORRENT_API_URL + "/auth/login",
            data={"username": QBITTORRENT_USERNAME, "password": QBITTORRENT_PASSWORD},
        ),
        False,
    )


async def logout_of_qbittorrent(session: requests):
    await makeRequest.make_request(
        session.post(QBITTORRENT_API_URL + "/auth/logout"), False
    )


async def delete_torrent(session: requests, torrent):
    await makeRequest.make_request(
        session.post(
            f"{QBITTORRENT_API_URL}/torrents/delete",
            data={
                "hashes": torrent["hash"],
                "deleteFiles": "true",
            },
        ),
        False,
    )


async def get_torrents(session: requests):
    return await makeRequest.make_request(
        session.get(QBITTORRENT_API_URL + "/torrents/info", params={"filter": "all"})
    )


def get_torrents_to_remove(torrents, category: str):
    torrents_to_remove = []
    if torrents:
        for torrent in torrents:
            if torrent["category"] == category:
                logging.debug(f'Processing {category} queue item: {torrent}')
                SHOULD_REMOVE_TORRENT = should_remove_torrent(torrent)
                
                if SHOULD_REMOVE_TORRENT[0] == True:
                    torrent_hash = torrent["hash"]
                    reason = SHOULD_REMOVE_TORRENT[1]
                    
                    if torrent_hash not in torrent_hit_counter:
                        torrent_hit_counter[torrent_hash] = {"hits": 0, "reason": reason}
                    
                    torrent_hit_counter[torrent_hash]["hits"] += 1
                    torrent_hit_counter[torrent_hash]["reason"] = reason
                    
                    current_hits = torrent_hit_counter[torrent_hash]["hits"]
                    logging.debug(f'{torrent["name"]} hit #{current_hits} for reason: {reason}')
                    
                    # Only mark for removal if we've hit the required consecutive count
                    if current_hits >= CONSECUTIVE_HITS_REQUIRED:
                        torrent["REMOVAL_REASON"] = SHOULD_REMOVE_TORRENT[1]
                        torrents_to_remove.append(torrent)
                        logging.debug(f'Marking {torrent["name"]} for removal after {current_hits} consecutive hits with reason: {reason}')
                    else:
                        logging.debug(f'{torrent["name"]} needs {CONSECUTIVE_HITS_REQUIRED - current_hits} more consecutive hits before removal')
                else:
                    torrent_hash = torrent["hash"]
                    if torrent_hash in torrent_hit_counter:
                        logging.debug(f'Resetting hit counter for {torrent["name"]} - torrent is now healthy')
                        del torrent_hit_counter[torrent_hash]
    
    return torrents_to_remove


def should_remove_torrent(torrent):
    download_speed_kbs = torrent["dlspeed"] / 1024
    remove_torrent = False
    reason = ""

    if torrent["state"] == "stalledDL":
        reason = "stalled"
        remove_torrent = True
    elif torrent["state"] == "metaDL":
        reason = "stuck downloading metadata"
        remove_torrent = True
    elif (
        DOWNLOAD_SPEED_CUTOFF
        and torrent["state"] == "downloading"
        and download_speed_kbs < float(DOWNLOAD_SPEED_CUTOFF)
    ):
        reason = f"slow ({download_speed_kbs}kb/s)"
        remove_torrent = True
    elif torrent["state"] == "downloading" and torrent["num_complete"] == 0:
        reason = "seedless"
        remove_torrent = True
    return remove_torrent, reason


def cleanup_hit_counter(torrents):
    """Clean up hit counter for torrents that are no longer in the list"""
    current_hashes = {torrent["hash"] for torrent in torrents}
    
    for torrent_hash in torrent_hit_counter:
        if torrent_hash not in current_hashes:
            logging.debug(f'Removing stale hit counter entry for hash: {torrent_hash}')
            del torrent_hit_counter[torrent_hash]    

async def remove_stalled_downloads(
    session: requests, torrents, category: str, api_url: str, api_key: str
):
    # Clean up stale entries in hit counter
    cleanup_hit_counter(torrents)
    
    torrents_to_remove = get_torrents_to_remove(torrents, category)
    if torrents_to_remove:
        queue = await arrAPI.get_queue(api_url, api_key)

        if queue is not None and "records" in queue:
            for torrent in torrents_to_remove:
                for item in queue["records"]:
                    if "title" in item and (
                        torrent["name"] in item["title"]
                        or item["title"] in torrent["name"]
                    ):
                        # Remove from hit counter since we're actually removing the torrent
                        torrent_hash = torrent["hash"]
                        if torrent_hash in torrent_hit_counter:
                            logging.debug(f'Removing hit counter entry for {torrent["name"]} after successful removal')
                            del torrent_hit_counter[torrent_hash]
                        
                        if category == "tv-sonarr":
                            SEASON_NUMBER = parse_season_number(item)
                            if SEASON_NUMBER:
                                await arrAPI.delete_queue_element(
                                    api_url,
                                    api_key,
                                    item,
                                    remove_from_client=False,
                                    blocklist=True,
                                )
                                await delete_torrent(session, torrent)
                                await arrAPI.search_sonarr_season(
                                    item["seriesId"], SEASON_NUMBER
                                )
                                logging.info(
                                    f"Removing {category}, Reason: {torrent['REMOVAL_REASON']}, Season: {SEASON_NUMBER}, Download: {item['title']}"
                                )
                            else:
                                await arrAPI.delete_queue_element(
                                    api_url,
                                    api_key,
                                    item,
                                    remove_from_client=True,
                                    blocklist=True,
                                )
                                logging.info(
                                    f"Removing {category}, Reason: {torrent['REMOVAL_REASON']}, Download: {item['title']}"
                                )
                                logging.warning(
                                    f"Did not re-search {category} download {item['title']}"
                                )
                        elif category == "radarr" or category == "radarr-4k":
                            await arrAPI.delete_queue_element(
                                api_url,
                                api_key,
                                item,
                                remove_from_client=False,
                                blocklist=True,
                            )
                            await delete_torrent(session, torrent)
                            await arrAPI.search_radarr_movie(item["movieId"])
                            logging.info(
                                f"Removing {category}, Reason: {torrent['REMOVAL_REASON']}, Download: {item['title']}"
                            )
                        else:
                            await arrAPI.delete_queue_element(
                                api_url,
                                api_key,
                                item,
                                remove_from_client=True,
                                blocklist=True,
                            )
                            logging.info(
                                f"Removing {category}, Reason: {torrent['REMOVAL_REASON']}, Download: {item['title']}"
                            )
                            logging.warning(
                                f"Did not re-search {category} download {item['title']}"
                            )
                        break


def parse_season_number(item):
    if "seasonNumber" in item:
        return item["seasonNumber"]
    else:
        PARSED_SEASON_AND_EPISODE_NUMBER = re.search(
            r"(s|S)\d\d+(?:\S){0,2}(e|E)\d\d+", item["title"]
        )
        if PARSED_SEASON_AND_EPISODE_NUMBER:
            PARSED_SEASON_NUMBER = re.search(
                r"(s|S)\d\d+",
                PARSED_SEASON_AND_EPISODE_NUMBER.group(0),
            )
            return int(PARSED_SEASON_NUMBER.group(0)[1:])

        PARSED_SEASON_NUMBER = re.search(r"(s|S)\d\d+", item["title"])
        if PARSED_SEASON_NUMBER:
            return int(PARSED_SEASON_NUMBER.group(0)[1:])
