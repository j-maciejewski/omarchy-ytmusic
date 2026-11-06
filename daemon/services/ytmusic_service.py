"""YouTube Music API service implementation with hybrid YouTube Data API v3 and ytmusicapi."""

import asyncio
import os
import re
import json
import logging
import time
import requests
from typing import List, Optional, Dict, Any
from ytmusicapi import YTMusic
import yt_dlp

from core.models import Track, Playlist
from core.interfaces import MusicRepository
from core.files import atomic_write_private_json

logger = logging.getLogger("ytmusic_service")


class YtMusicService(MusicRepository):
    """Adapter for interacting with YouTube Music API and audio stream extraction."""

    def __init__(self, auth_file_path: Optional[str] = None):
        self.auth_file_path = auth_file_path
        self._ytmusic = YTMusic()  # Anonymous client for search, exploration and radio
        self._stream_cache: Dict[str, tuple[str, float]] = {}  # vid -> (url, timestamp)
        self._ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extract_flat": False,
            "skip_download": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android"]
                }
            },
        }

        self._ydl = yt_dlp.YoutubeDL(self._ydl_opts)
        # OAuth configuration
        self._oauth_config: Optional[Dict[str, Any]] = None
        self._load_oauth_config()

    def _load_oauth_config(self) -> None:
        """Load OAuth tokens and credentials if available."""
        if not self.auth_file_path or not os.path.exists(self.auth_file_path):
            return
        
        # Proactively secure credentials file if created manually with default permissions
        try:
            os.chmod(self.auth_file_path, 0o600)
        except Exception:
            pass
            
        try:
            with open(self.auth_file_path, "r") as f:
                data = json.load(f)
            if "refresh_token" in data and "client_id" in data and "client_secret" in data:
                self._oauth_config = data
                logger.info("Loaded Google OAuth 2.0 credentials successfully.")
        except Exception as ex:
            logger.warning("Failed to parse OAuth config: %s", ex)

    def _get_access_token(self) -> Optional[str]:
        """Obtain a valid OAuth access token, auto-refreshing when expired."""
        if not self._oauth_config:
            return None

        # Check if current access token is still fresh
        expires_at = self._oauth_config.get("expires_at", 0)
        access_token = self._oauth_config.get("access_token")
        if access_token and time.time() < (expires_at - 60):
            return access_token

        # Refresh token via Google OAuth2 endpoint
        try:
            resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self._oauth_config["client_id"],
                    "client_secret": self._oauth_config["client_secret"],
                    "refresh_token": self._oauth_config["refresh_token"],
                    "grant_type": "refresh_token",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                self._oauth_config["access_token"] = new_token
                self._oauth_config["expires_at"] = int(time.time()) + expires_in
                atomic_write_private_json(self.auth_file_path, self._oauth_config)
                logger.info("Successfully refreshed Google OAuth 2.0 access token.")
                return new_token
            else:
                logger.error("Failed to refresh OAuth token: %s", resp.text)
                return None
        except Exception as ex:
            logger.error("Error refreshing OAuth token: %s", ex)
            return None

    def is_authenticated(self) -> bool:
        """Check if client is running with user authentication credentials."""
        return self._oauth_config is not None

    async def search(self, query: str) -> List[Track]:
        """Search tracks with async executor to avoid blocking the event loop."""
        if not query.strip():
            return []

        def _do_search() -> List[Track]:
            try:
                # Only official songs; regular YouTube videos are excluded.
                results = self._ytmusic.search(query=query, filter="songs", limit=20)
                tracks: List[Track] = []
                seen = set()
                for item in results:
                    video_id = item.get("videoId")
                    if not video_id or video_id in seen:
                        continue
                    seen.add(video_id)
                    artists = ", ".join(a.get("name", "") for a in item.get("artists", [])) or "Unknown Artist"
                    album = item.get("album", {}).get("name", "") if item.get("album") else ""
                    duration_sec = int(item.get("duration_seconds", 0) or 0)
                    thumbnails = item.get("thumbnails") or item.get("thumbnail") or []
                    thumb_url = thumbnails[-1].get("url", "") if thumbnails else ""
                    tracks.append(
                        Track(
                            video_id=video_id,
                            title=item.get("title", "Unknown Title"),
                            artist=artists,
                            album=album,
                            duration_seconds=duration_sec,
                            thumbnail_url=thumb_url,
                        )
                    )
                return tracks
            except Exception as ex:
                logger.error("Search error for '%s': %s", query, ex)
                return []

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_search)

    async def search_albums(self, query: str) -> List[Playlist]:
        """Search albums; each result's playlist_id is the MPREb_ browseId used by get_playlist_tracks."""
        if not query.strip():
            return []

        def _do_search() -> List[Playlist]:
            try:
                results = self._ytmusic.search(query=query, filter="albums", limit=20)
                albums: List[Playlist] = []
                for item in results:
                    browse_id = item.get("browseId")
                    if not browse_id:
                        continue
                    artists = ", ".join(a.get("name", "") for a in item.get("artists", [])) or "Unknown Artist"
                    # e.g. "Album • 1997" / "EP • 2021"
                    details = " • ".join(str(p) for p in (item.get("type"), item.get("year")) if p)
                    thumbnails = item.get("thumbnails") or []
                    thumb_url = thumbnails[-1].get("url", "") if thumbnails else ""
                    albums.append(
                        Playlist(
                            playlist_id=browse_id,
                            title=item.get("title", "Unknown Album"),
                            description=details,
                            track_count=0,
                            thumbnail_url=thumb_url,
                            author=artists,
                        )
                    )
                return albums
            except Exception as ex:
                logger.error("Album search error for '%s': %s", query, ex)
                return []

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_search)

    async def get_home(self) -> List[dict]:
        """Retrieve home feed suggestions and inject Liked Music as Quick Picks."""
        # 1. Fetch user's Liked Music to build a custom Quick Picks shelf
        liked_tracks = await self.get_playlist_tracks("LM")
        
        # 2. Fetch generic/anonymous home feed in executor
        def _fetch_home() -> List[dict]:
            try:
                return self._ytmusic.get_home(limit=8)
            except Exception as ex:
                logger.error("Error fetching home suggestions: %s", ex)
                return []
        
        loop = asyncio.get_running_loop()
        home_feed = await loop.run_in_executor(None, _fetch_home)
        
        # 3. Inject "Selección Rápida" shelf
        if liked_tracks:
            import random
            top_liked = [t for t in liked_tracks if t.is_song][:40]
            random.shuffle(top_liked)
            quick_picks = top_liked[:15]
            
            custom_shelf = {
                "title": "Selección Rápida",
                "contents": []
            }
            for t in quick_picks:
                custom_shelf["contents"].append({
                    "title": t.title,
                    "videoId": t.video_id,
                    "thumbnails": [{"url": t.thumbnail_url, "width": 544, "height": 544}],
                    "description": t.artist
                })
            
            home_feed.insert(0, custom_shelf)
            
        return home_feed

    async def get_playlists(self) -> List[Playlist]:
        """Retrieve user library playlists exclusive to YouTube Music via OAuth."""
        def _fetch_playlists() -> List[Playlist]:
            playlists: List[Playlist] = []
            try:
                token = self._get_access_token()
                if token:
                    # 1. Fetch YouTube Music exclusive playlists
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0) Gecko/20100101 Firefox/88.0",
                        "Origin": "https://music.youtube.com",
                    }
                    resp = requests.post(
                        "https://music.youtube.com/youtubei/v1/browse?prettyPrint=false",
                        headers=headers,
                        json={
                            "context": {
                                "client": {
                                    "clientName": "TVHTML5",
                                    "clientVersion": "7.20260814.01.00",
                                    "gl": "US",
                                    "hl": "en",
                                }
                            },
                            "browseId": "FEmusic_liked_playlists",
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        def find_tiles(obj):
                            tiles = []
                            if isinstance(obj, dict):
                                if "tileRenderer" in obj:
                                    tiles.append(obj["tileRenderer"])
                                for v in obj.values():
                                    tiles.extend(find_tiles(v))
                            elif isinstance(obj, list):
                                for item in obj:
                                    tiles.extend(find_tiles(item))
                            return tiles

                        for t in find_tiles(data):
                            meta = t.get("metadata", {}).get("tileMetadataRenderer", {})
                            title = meta.get("title", {}).get("runs", [{}])[0].get("text", "Untitled Playlist")
                            cmd = t.get("onSelectCommand", {}).get("browseEndpoint", {})
                            browse_id = cmd.get("browseId", "")
                            if not browse_id:
                                continue
                            clean_id = browse_id.replace("VL", "") if browse_id.startswith("VL") else browse_id
                            
                            # Extract thumbnail
                            thumbs = t.get("header", {}).get("tileHeaderRenderer", {}).get("thumbnail", {}).get("thumbnails", [])
                            thumb_url = thumbs[-1].get("url", "") if thumbs else ""
                            
                            playlists.append(Playlist(
                                playlist_id=clean_id,
                                title=title,
                                description="",
                                track_count=0,
                                thumbnail_url=thumb_url,
                                author="YouTube Music",
                            ))

                # Fallback to home featured sections if empty
                if not playlists:
                    home_sections = self._ytmusic.get_home(limit=8)
                    seen = set()
                    for section in home_sections:
                        section_title = section.get("title", "")
                        for item in section.get("contents", []):
                            playlist_id = item.get("playlistId") or item.get("browseId", "")
                            if not playlist_id or playlist_id in seen:
                                continue
                            if playlist_id.startswith("UC") or playlist_id.startswith("FE"):
                                continue
                            seen.add(playlist_id)
                            title = item.get("title", "")
                            if not title:
                                continue
                            thumbnails = item.get("thumbnails") or item.get("thumbnail") or []
                            thumb_url = thumbnails[-1].get("url", "") if thumbnails else ""
                            playlists.append(Playlist(
                                playlist_id=playlist_id, title=title,
                                description=section_title, track_count=0,
                                thumbnail_url=thumb_url, author=section_title,
                            ))

                return playlists
            except Exception as ex:
                logger.error("Error fetching playlists: %s", ex)
                return []

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch_playlists)

    async def get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        """Retrieve tracks within a specific playlist via YouTube Data API v3 or ytmusic."""
        def _fetch_tracks() -> List[Track]:
            try:
                token = self._get_access_token()
                tracks: List[Track] = []

                def _parse_iso8601(duration_str: str) -> int:
                    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
                    if not match:
                        return 0
                    h, m, s = match.groups()
                    return int(h or 0) * 3600 + int(m or 0) * 60 + int(s or 0)

                if token and (playlist_id == "LM" or playlist_id.startswith("PL") or playlist_id.startswith("FL") or playlist_id.startswith("LL")):
                    # Fetch All Playlist Items via YouTube API v3 with Pagination
                    page_token = None
                    target_pid = "LM" if (playlist_id == "LM" or playlist_id == "VLLM") else playlist_id
                    
                    while len(tracks) < 500:
                        params = {
                            "part": "snippet,contentDetails",
                            "playlistId": target_pid,
                            "maxResults": 50,
                        }
                        if page_token:
                            params["pageToken"] = page_token

                        resp = requests.get(
                            "https://www.googleapis.com/youtube/v3/playlistItems",
                            headers={"Authorization": f"Bearer {token}"},
                            params=params,
                            timeout=10,
                        )
                        if resp.status_code != 200:
                            break
                        
                        data = resp.json()
                        items = data.get("items", [])
                        if not items:
                            break

                        for item in items:
                            snip = item.get("snippet", {})
                            content = item.get("contentDetails", {})
                            vid = snip.get("resourceId", {}).get("videoId") or content.get("videoId")
                            if not vid:
                                continue
                            thumbs = snip.get("thumbnails", {})
                            # Prefer 'maxres' (16:9) or 'medium' (16:9) over 'high' (4:3) 
                            # because 4:3 thumbnails have baked-in black bars that ruin 1:1 UI crops.
                            thumb_node = thumbs.get("maxres") or thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}
                            thumb_url = thumb_node.get("url", "")
                            title = snip.get("title", "Unknown Title")
                            artist_raw = snip.get("videoOwnerChannelTitle") or snip.get("channelTitle", "Unknown Artist")
                            # Clean up '- Topic' from YouTube Music channel names
                            artist = artist_raw.replace(" - Topic", "").strip() if artist_raw else "Unknown Artist"

                            tracks.append(Track(
                                video_id=vid,
                                title=title,
                                artist=artist,
                                album="",
                                duration_seconds=0,
                                thumbnail_url=thumb_url,
                                # Only YouTube Music songs are uploaded by "<Artist> - Topic" channels
                                is_song=(artist_raw or "").endswith(" - Topic"),
                            ))

                        page_token = data.get("nextPageToken")
                        if not page_token:
                            break

                    if tracks:
                        return tracks

                # Public / Explore playlist fallback via ytmusicapi
                if playlist_id.startswith("MPREb_"):
                    # It's an album!
                    playlist_data = self._ytmusic.get_album(browseId=playlist_id)
                    self._prefer_album_audio_tracks(playlist_data)
                else:
                    playlist_data = self._ytmusic.get_playlist(playlistId=playlist_id, limit=100)
                
                # Album tracks have no thumbnails of their own and a plain-string "album" field
                album_thumbnails = playlist_data.get("thumbnails") or []
                for item in playlist_data.get("tracks", []):
                    video_id = item.get("videoId")
                    if not video_id:
                        continue
                    artists = ", ".join(a.get("name", "") for a in item.get("artists", [])) or "Unknown Artist"
                    album_field = item.get("album")
                    album = album_field.get("name", "") if isinstance(album_field, dict) else (album_field or "")
                    duration_sec = int(item.get("duration_seconds", 0) or 0)
                    thumbnails = item.get("thumbnails") or item.get("thumbnail") or album_thumbnails
                    thumb_url = thumbnails[-1].get("url", "") if thumbnails else ""
                    tracks.append(
                        Track(
                            video_id=video_id,
                            title=item.get("title", "Unknown Title"),
                            artist=artists,
                            album=album,
                            duration_seconds=duration_sec,
                            thumbnail_url=thumb_url,
                            is_song=item.get("videoType", "MUSIC_VIDEO_TYPE_ATV") == "MUSIC_VIDEO_TYPE_ATV",
                        )
                    )
                return tracks
            except Exception as ex:
                logger.error("Error fetching playlist tracks for %s: %s", playlist_id, ex)
                return []

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch_tracks)

    def _prefer_album_audio_tracks(self, album_data: dict) -> None:
        """Swap album track videoIds for their audio-only versions, in place.

        Album tracks that have an official music video point at the video (with intros,
        skits, etc). The album's audioPlaylistId lists the audio-only tracks in the same order.
        """
        audio_playlist_id = album_data.get("audioPlaylistId")
        tracks = album_data.get("tracks", [])
        if not audio_playlist_id or not tracks:
            return
        try:
            audio_tracks = self._ytmusic.get_playlist(playlistId=audio_playlist_id, limit=None).get("tracks", [])
        except Exception as ex:
            logger.warning("Could not fetch audio playlist %s: %s", audio_playlist_id, ex)
            return
        # Only trust the positional mapping when both lists line up
        if len(audio_tracks) != len(tracks):
            return
        for track, audio in zip(tracks, audio_tracks):
            if audio.get("videoId") and audio.get("title") == track.get("title"):
                track["videoId"] = audio["videoId"]

    async def get_watch_playlist(self, video_id: str, limit: int = 25) -> List[Track]:
        """Retrieve dynamic radio watch playlist based on a track."""
        def _fetch_radio() -> List[Track]:
            try:
                radio_data = self._ytmusic.get_watch_playlist(videoId=video_id, limit=limit)
                tracks: List[Track] = []
                for item in radio_data.get("tracks", []):
                    vid = item.get("videoId")
                    if not vid or vid == video_id:
                        continue
                    # Skip music videos and user uploads; keep only songs
                    if item.get("videoType", "MUSIC_VIDEO_TYPE_ATV") != "MUSIC_VIDEO_TYPE_ATV":
                        continue
                    artists = ", ".join(a.get("name", "") for a in item.get("artists", [])) or "Unknown Artist"
                    album = item.get("album", {}).get("name", "") if item.get("album") else ""
                    duration_sec = int(item.get("duration_seconds", 0) or 0)
                    thumbnails = item.get("thumbnails") or item.get("thumbnail") or []
                    thumb_url = thumbnails[-1].get("url", "") if thumbnails else ""
                    tracks.append(
                        Track(
                            video_id=vid,
                            title=item.get("title", "Unknown Title"),
                            artist=artists,
                            album=album,
                            duration_seconds=duration_sec,
                            thumbnail_url=thumb_url,
                        )
                    )
                return tracks
            except Exception as ex:
                logger.error("Error fetching watch playlist for %s: %s", video_id, ex)
                return []

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch_radio)

    async def get_track_album(self, video_id: str) -> Optional[Playlist]:
        """Look up the album of a song; its watch-playlist entry carries the album browseId."""
        if not video_id:
            return None

        def _fetch_album() -> Optional[Playlist]:
            try:
                watch_data = self._ytmusic.get_watch_playlist(videoId=video_id, limit=1)
                for item in watch_data.get("tracks", []):
                    if item.get("videoId") != video_id:
                        continue
                    album = item.get("album") or {}
                    if not album.get("id"):
                        return None
                    artists = ", ".join(a.get("name", "") for a in item.get("artists", []))
                    thumbnails = item.get("thumbnail") or item.get("thumbnails") or []
                    return Playlist(
                        playlist_id=album["id"],
                        title=album.get("name") or "Unknown Album",
                        thumbnail_url=thumbnails[-1].get("url", "") if thumbnails else "",
                        author=artists,
                    )
                return None
            except Exception as ex:
                logger.error("Error resolving album for %s: %s", video_id, ex)
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch_album)

    async def get_stream_url(self, video_id: str) -> Optional[str]:
        """Extract fresh streamable audio URL using optimized persistent yt-dlp instance."""
        # 1. Check TTL cache (valid for 3 hours)
        now = time.time()
        if video_id in self._stream_cache:
            cached_url, cached_time = self._stream_cache[video_id]
            if (now - cached_time) < 10800:  # 3 hours
                return cached_url

        def _extract_url() -> Optional[str]:
            url = f"https://www.youtube.com/watch?v={video_id}"
            try:
                info = self._ydl.extract_info(url, download=False)
                if not info:
                    return None
                stream_url = info.get("url")
                if not stream_url and "formats" in info:
                    audio_formats = [
                        f for f in info["formats"]
                        if f.get("vcodec") == "none" and f.get("acodec") != "none"
                    ]
                    if audio_formats:
                        stream_url = audio_formats[-1].get("url")

                if stream_url:
                    self._stream_cache[video_id] = (stream_url, time.time())
                return stream_url
            except Exception as ex:
                logger.error("Error extracting stream URL for %s: %s", video_id, ex)
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _extract_url)

    async def add_track_to_playlist(self, playlist_id: str, video_id: str) -> dict:
        """Add track to a user playlist via YouTube Data API v3 with duplicate checking."""
        def _add() -> dict:
            try:
                token = self._get_access_token()
                if not token:
                    return {"success": False, "already_exists": False, "error": "No token"}

                # 1. Check if track is already in the playlist
                existing_tracks = requests.get(
                    "https://www.googleapis.com/youtube/v3/playlistItems",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"part": "contentDetails", "playlistId": playlist_id, "videoId": video_id, "maxResults": 1},
                    timeout=5,
                )
                if existing_tracks.status_code == 200:
                    items = existing_tracks.json().get("items", [])
                    if len(items) > 0:
                        return {"success": False, "already_exists": True}

                resp = requests.post(
                    "https://www.googleapis.com/youtube/v3/playlistItems",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    params={"part": "snippet"},
                    json={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }
                    },
                    timeout=10,
                )
                return {"success": resp.status_code in (200, 201), "already_exists": False}
            except Exception as ex:
                logger.error("Failed adding track %s to playlist %s: %s", video_id, playlist_id, ex)
                return {"success": False, "already_exists": False, "error": str(ex)}

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _add)

    async def create_playlist(self, title: str, description: str = "") -> Optional[str]:
        """Create a new playlist in user account via YouTube Data API v3."""
        def _create() -> Optional[str]:
            try:
                token = self._get_access_token()
                if not token:
                    return None
                resp = requests.post(
                    "https://www.googleapis.com/youtube/v3/playlists",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    params={"part": "snippet,status"},
                    json={
                        "snippet": {"title": title, "description": description},
                        "status": {"privacyStatus": "private"},
                    },
                    timeout=10,
                )
                if resp.status_code in (200, 201):
                    return resp.json().get("id")
                return None
            except Exception as ex:
                logger.error("Failed creating playlist '%s': %s", title, ex)
                return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _create)

    async def rate_track(self, video_id: str, rating: str) -> bool:
        """Set track rating ('LIKE', 'DISLIKE', 'INDIFFERENT'/'NONE') via YouTube Data API v3."""
        def _rate() -> bool:
            try:
                token = self._get_access_token()
                if not token:
                    logger.error("Cannot rate track %s: No OAuth access token.", video_id)
                    return False
                yt_rating = "like" if rating.upper() == "LIKE" else ("dislike" if rating.upper() == "DISLIKE" else "none")
                resp = requests.post(
                    "https://www.googleapis.com/youtube/v3/videos/rate",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"id": video_id, "rating": yt_rating},
                    timeout=10,
                )
                return resp.status_code in (200, 204)
            except Exception as ex:
                logger.error("Failed setting rating %s for track %s: %s", rating, video_id, ex)
                return False

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _rate)
