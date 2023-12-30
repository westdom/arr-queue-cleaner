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
                logging.debug(f'Processing {category} queue item: {torrent["name"]}')
                SHOULD_REMOVE_TORRENT = should_remove_torrent(torrent)
                if SHOULD_REMOVE_TORRENT[0] == True:
                    torrent["REMOVAL_REASON"] = SHOULD_REMOVE_TORRENT[1]
                    torrents_to_remove.append(torrent)
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


async def remove_stalled_downloads(
    session: requests, torrents, category: str, api_url: str, api_key: str
):
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
                                    f"Removing {torrent['REMOVAL_REASON']} download: {item['series']['title'] if 'series' in item else item['title']} S{SEASON_NUMBER}"
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
                                    f"Removing {torrent['REMOVAL_REASON']} download: {item['series']['title'] if 'series' in item else item['title']}"
                                )
                                logging.warning(
                                    f"Did not re-search sonarr download {item['series']['title'] if 'series' in item else item['title']}"
                                )
                        elif category == "radarr":
                            await arrAPI.delete_queue_element(
                                api_url,
                                api_key,
                                item,
                                remove_from_client=False,
                                blocklist=True,
                            )
                            await delete_torrent(session, torrent)
                            logging.info(
                                f"Removing {torrent['REMOVAL_REASON']} download: {item['title']}"
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
                                f"Removing {torrent['REMOVAL_REASON']} download: {item['movie']['title'] if 'movie' in item else item['title']}"
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


def parse_episode_number(item):
    if "episode" in item and "episodeNumber" in item["episode"]:
        return item["episode"]["episodeNumber"]
    else:
        PARSED_SEASON_AND_EPISODE_NUMBER = re.search(
            r"(s|S)\d\d+(?:\S){0,2}(e|E)\d\d+", item["title"]
        )
        if PARSED_SEASON_AND_EPISODE_NUMBER:
            PARSED_EPISODE_NUMBER = re.search(
                r"(e|E)\d\d+",
                PARSED_SEASON_AND_EPISODE_NUMBER.group(0),
            )
            return int(PARSED_EPISODE_NUMBER.group(0)[1:])
