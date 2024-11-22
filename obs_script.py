import json
import os
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pygame
import requests

from db import connect
import boto3


AWS_ENDPOINT_URL_S3 = os.environ.get("AWS_ENDPOINT_URL_S3")
AWS_BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME")

svc = boto3.client("s3", endpoint_url=AWS_ENDPOINT_URL_S3)


class PlayerState:
    def __init__(self):
        self.current_track = 0
        self.is_playing = False
        self.current_image = ""
        self.current_title = ""
        self._lock = threading.Lock()

    def update(self, track, image, title, playing):
        with self._lock:
            self.current_track = track
            self.current_image = image
            self.current_title = title
            self.is_playing = playing

    def get_info(self):
        with self._lock:
            return {
                "image_url": self.current_image,
                "title": self.current_title,
                "is_playing": self.is_playing,
                "track": self.current_track,
            }


class WebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, player_state=None, **kwargs):
        self.player_state = player_state
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()

            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Now Playing</title>
                <style>
                    body {
                        margin: 0;
                        padding: 0;
                        background: transparent;
                        overflow: hidden;
                    }
                    .container {
                        width: 100vw;
                        height: 100vh;
                        display: flex;
                        flex-direction: column;
                        align-items: center;
                    }
                    .image-container {
                        width: 100%;
                        height: 100%;
                        position: relative;
                    }
                    img {
                        width: 100%;
                        height: 100%;
                        object-fit: cover;
                        transition: opacity 0.5s ease-in-out;
                    }
                </style>
                <script>
                    let currentTrack = -1;
                    
                    function updateContent() {
                        fetch('/info')
                            .then(response => response.json())
                            .then(data => {
                                if (currentTrack !== data.track) {
                                    document.getElementById('current-image').src = data.image_url;
                                    currentTrack = data.track;
                                }
                            });
                    }
                    
                    setInterval(updateContent, 500);
                </script>
            </head>
            <body>
                <div class="container">
                    <div class="image-container">
                        <img id="current-image" src="" alt="">
                    </div>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode())

        elif self.path == "/title":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()

            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Track Title</title>
                <style>
                    body {
                        margin: 0;
                        padding: 0;
                        background: transparent;
                        overflow: hidden;
                        font-family: 'Arial', sans-serif;
                    }
                    .title-container {
                        width: 400px; /* Narrow fixed width to force wrapping */
                        padding: 10px 20px;
                        box-sizing: border-box;
                        display: flex;
                        align-items: flex-start;
                    }
                    .title-text {
                        color: white;
                        font-size: 24px;
                        font-weight: bold;
                        text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
                        white-space: normal;
                        flex: 1;
                        line-height: 1.3;
                        word-wrap: break-word;
                        max-width: 350px; /* Ensure about 5 words per line */
                        animation: fadeInOut 1s ease-in-out;
                    }
                    .status-indicator {
                        display: inline-block;
                        min-width: 8px;
                        height: 8px;
                        border-radius: 50%;
                        margin-right: 10px;
                        margin-top: 10px;
                        animation: pulse 2s infinite;
                        flex-shrink: 0;
                    }
                    .playing {
                        background-color: #4CAF50;
                    }
                    .paused {
                        background-color: #FFA500;
                    }
                    @keyframes fadeInOut {
                        0% { opacity: 0; transform: translateY(-10px); }
                        100% { opacity: 1; transform: translateY(0); }
                    }
                    @keyframes pulse {
                        0% { transform: scale(1); opacity: 1; }
                        50% { transform: scale(1.2); opacity: 0.7; }
                        100% { transform: scale(1); opacity: 1; }
                    }
                </style>
                <script>
                    function updateTitle() {
                        fetch('/info')
                            .then(response => response.json())
                            .then(data => {
                                const titleElement = document.getElementById('title');
                                const statusDot = document.getElementById('status-dot');
                                
                                if (titleElement.textContent !== data.title) {
                                    titleElement.style.animation = 'none';
                                    titleElement.offsetHeight; // Trigger reflow
                                    titleElement.style.animation = 'fadeInOut 1s ease-in-out';
                                    titleElement.textContent = data.title;
                                }
                                
                                statusDot.className = 'status-indicator ' + 
                                    (data.is_playing ? 'playing' : 'paused');
                            });
                    }
                    
                    setInterval(updateTitle, 500);
                </script>
            </head>
            <body>
                <div class="title-container">
                    <span id="status-dot" class="status-indicator"></span>
                    <span id="title" class="title-text"></span>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode())

        elif self.path == "/info":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            info = self.player_state.get_info()
            self.wfile.write(json.dumps(info).encode())


class AudioPlayer:
    def __init__(self, port=8000, refresh_interval=60):
        pygame.init()
        pygame.mixer.init()

        self.state = PlayerState()
        self.tracks = []
        self.current_track = 0
        self.refresh_interval = refresh_interval
        self.last_track_count = 0
        self.playing_reporter = False  # Track if we're playing reporter audio

        self.server_thread = threading.Thread(
            target=self.run_server, args=(port,), daemon=True
        )
        self.server_thread.start()

        self.refresh_thread = threading.Thread(
            target=self.refresh_tracks_periodically, daemon=True
        )
        self.refresh_thread.start()

        self.MUSIC_END = pygame.USEREVENT + 1
        pygame.mixer.music.set_endevent(self.MUSIC_END)

        self.load_tracks()

        print(f"Web display available at http://localhost:{port}")
        webbrowser.open(f"http://localhost:{port}")

    def run_server(self, port):
        """Run the web server"""

        def handler(*args, **kwargs):
            return WebHandler(*args, player_state=self.state, **kwargs)

        server = HTTPServer(("localhost", port), handler)
        server.serve_forever()

    def refresh_tracks_periodically(self):
        """Periodically check for new tracks in the database"""
        while True:
            time.sleep(self.refresh_interval)
            if self.load_tracks():
                print("New tracks found in database")

    def load_tracks(self):
        conn = connect()
        cursor = conn.cursor()

        # Calculate the timestamp for 12 hours ago
        twelve_hours_ago = datetime.now() - timedelta(hours=12)

        # Execute the query with the timestamp filter
        cursor.execute(
            "SELECT audio_path_stored, audio_path_reporter, urltoimage, headline FROM news WHERE created_at >= %s ORDER BY created_at DESC",
            (twelve_hours_ago,),
        )
        new_tracks = cursor.fetchall()
        conn.close()

        if len(new_tracks) > self.last_track_count:

            # The tracks are just path in aws s3, we need to download locally
            for track in new_tracks:
                audio_path = track[0]
                reporter_path = track[1]

                # Download the audio file
                if audio_path:
                    if not os.path.exists(f"audios/{audio_path}"):
                        url_to_download = (
                            f"{AWS_ENDPOINT_URL_S3}/{AWS_BUCKET_NAME}/{audio_path}"
                        )
                        download_response = requests.get(url_to_download)

                        if download_response.status_code == 200:
                            with open(f"audios/{audio_path}", "wb") as f:
                                f.write(download_response.content)
                            print(f"Downloaded audio file: {audio_path}")

                # Download the reporter audio file
                if reporter_path:
                    if not os.path.exists(f"audios/{reporter_path}"):
                        url_to_download = (
                            f"{AWS_ENDPOINT_URL_S3}/{AWS_BUCKET_NAME}/{reporter_path}"
                        )
                        download_response = requests.get(url_to_download)

                        if download_response.status_code == 200:
                            with open(f"audios/{reporter_path}", "wb") as f:
                                f.write(download_response.content)
                            print(f"Downloaded reporter audio file: {reporter_path}")

            self.tracks = new_tracks
            self.last_track_count = len(new_tracks)
            print(f"Updated track list. Total tracks: {len(self.tracks)}")
            self.current_track = 0
            return True
        return False

    def play_next(self):
        if not self.tracks:
            print("No tracks available. Waiting for new tracks...")
            time.sleep(5)
            if self.load_tracks() and self.tracks:
                pass
            else:
                return

        try:
            audio_path, reporter_path, image_url, title = self.tracks[
                self.current_track
            ]

            # Check which file to play
            current_path = (
                f"audios/{reporter_path}"
                if self.playing_reporter
                else f"audios/{audio_path}"
            )

            if self.playing_reporter:
                with open("speaker.txt", "w") as f:
                    f.write("reporter")
            else:
                with open("speaker.txt", "w") as f:
                    f.write("anchor")

            if not os.path.exists(current_path):
                print(f"Audio file not found: {current_path}")
                if not self.playing_reporter:
                    # If main audio not found, skip to next track
                    self.advance_track()
                    return self.play_next()
                else:
                    # If reporter audio not found, move to next track
                    self.playing_reporter = False
                    self.advance_track()
                    return self.play_next()

            pygame.mixer.music.load(current_path)
            pygame.mixer.music.play()
            self.state.update(self.current_track, image_url, title, True)
            print(
                f"Now playing: {title} ({'Reporter' if self.playing_reporter else 'Main'})"
            )

        except Exception as e:
            print(f"Error playing track: {e}")
            if not self.playing_reporter:
                self.advance_track()
            else:
                self.playing_reporter = False
            return self.play_next()

    def advance_track(self):
        with open("speaker.txt", "w") as f:
            f.write("break")

        # Wait 3 seconds
        time.sleep(3)

        """Move to next track"""
        if self.tracks:
            self.current_track = (self.current_track + 1) % len(self.tracks)

    def handle_track_end(self):
        if not self.playing_reporter:
            # If reporter audio exists, play it next
            if self.tracks[self.current_track][1]:
                self.playing_reporter = True
                self.play_next()
            else:
                # No reporter audio, move to next track
                self.advance_track()
                self.play_next()
        else:
            # Reporter audio finished, move to next track
            self.playing_reporter = False
            self.advance_track()
            self.play_next()

    def run(self):
        clock = pygame.time.Clock()
        running = True

        if self.tracks:
            self.play_next()

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_SPACE:
                        if self.state.is_playing:
                            pygame.mixer.music.pause()
                            self.state.update(
                                self.current_track,
                                self.tracks[self.current_track][
                                    2
                                ],  # image_url is now at index 2
                                self.tracks[self.current_track][
                                    3
                                ],  # title is now at index 3
                                False,
                            )
                        else:
                            pygame.mixer.music.unpause()
                            self.state.update(
                                self.current_track,
                                self.tracks[self.current_track][2],
                                self.tracks[self.current_track][3],
                                True,
                            )
                    elif event.key == pygame.K_RIGHT:
                        self.playing_reporter = False
                        self.advance_track()
                        self.play_next()
                    elif event.key == pygame.K_LEFT:
                        self.playing_reporter = False
                        self.current_track = (self.current_track - 1) % len(self.tracks)
                        self.play_next()
                elif event.type == self.MUSIC_END:
                    self.handle_track_end()

            clock.tick(30)

        pygame.quit()


if __name__ == "__main__":
    player = AudioPlayer()
    player.run()
