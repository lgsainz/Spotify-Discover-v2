from datetime import datetime, timedelta, date
from flask import request, session
from dotenv import load_dotenv
from cache import TokenCache
import helpers as hp
import numpy as np
import requests
import json
import os


load_dotenv()  # load env variables

# client info
CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI')  # redirect to after granting user permission
USER_ID = os.getenv('SPOTIFY_USER_ID')


class SpotifyClient:

    def __init__(self):
        self.CACHE = TokenCache()

    """Request access and refresh tokens"""
    def request_api_tokens(self, code):
        if code == '':
            raise Exception('No authorization code found.')
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI,
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET
        }
        response = requests.post('https://accounts.spotify.com/api/token', data=payload)

        if response.status_code != 200:
            return f'Something went wrong - {response.status_code}'

        content = response.json()
        expires_in = content.get('expires_in')
        self.CACHE.save_token('access_token', content.get('access_token'), expires_in)
        self.CACHE.save_token('refresh_token', content.get('refresh_token'), 8600)  # save 'forever'
        print('Successfully completed Auth flow!')

        return content

    """Retrieve the token from cache"""
    def get_token_from_cache(self):
        cached_token = self.CACHE.get_token('access_token')

        if not cached_token:
            cached_refresh_token = self.CACHE.get_token('refresh_token')

            if not cached_refresh_token:
                print('No refresh token available. Retrieving new tokens.')
                new_tokens = self.request_api_tokens()
                return new_tokens.get('access_token')

            return self.refresh_tokens(cached_refresh_token)

        return cached_token

    """Refresh access token"""
    def refresh_tokens(self, refresh_token):
        pass

    """Prep request headers"""
    def set_request_headers(self):
        access_token = self.get_token_from_cache()
        headers = {'Authorization': f'Bearer {access_token}'}
        return headers

    """Get current user's followed artists"""
    def get_artists(self):
        headers = self.set_request_headers()
        response = requests.get('https://api.spotify.com/v1/me/following?type=artist', headers=headers)
        content = response.json()

        artist_ids = []
        artists = content['artists']['items']
        for artist in artists:
            artist_ids.append(artist['id'])

        # While next results page exists, get it and its artist_ids
        while content['artists']['next']:
            next_page_uri = content['artists']['next']
            response = requests.get(next_page_uri, headers=headers)

            if response.status_code == 401:  # bad or expired token
                headers = self.set_request_headers()
                response = requests.get(next_page_uri, headers=headers)

            content = response.json()
            for artist in content['artists']['items']:
                artist_ids.append(artist['id'])

        print('Retrieved artist IDs!')
        return artist_ids

    """Get all albums by followed artists (albums, singles)"""
    def get_albums(self, artist_ids):
        headers = self.set_request_headers()
        album_ids = []
        album_names = {}  # used to check for duplicates with different id's * issue with some albums

        # set time frame for new releases (4 weeks)
        today = datetime.now()
        number_weeks = timedelta(weeks=4)
        time_frame = (today - number_weeks).date()

        for id in artist_ids:
            uri = f'https://api.spotify.com/v1/artists/{id}/albums?include_groups=album,single&country=US'
            response = requests.get(uri, headers=headers)

            if response.status_code == 401:  # bad or expired token
                headers = self.set_request_headers()
                response = requests.get(headers=headers)

            content = response.json()

            albums = content['items']
            for album in albums:
                # check for new releases
                try:
                    release_date = datetime.strptime(album['release_date'], '%Y-%m-%d')
                    album_name = album['name']
                    artist_name = album['artists'][0]['name']
                    if release_date.date() > time_frame:
                        # if we do find a duplicate album name, check if it's by a different artist
                        if album_name not in album_names or artist_name != album_names[album_name]:
                            album_ids.append(album['id'])
                            album_names[album_name] = artist_name
                except ValueError:
                    # there appear to be some older release dates that only contain year (2007) - irrelevant
                    print(f'Release date found with format: {album["release_date"]}')

        print('Retrieved album IDs!')
        return album_ids

    """Get each individual album's track uri's"""
    def get_tracks(self, album_ids):
        track_uris = []
        headers = self.set_request_headers()

        for id in album_ids:
            uri = f'https://api.spotify.com/v1/albums/{id}/tracks'
            response = requests.get(uri, headers=headers)

            if response.status_code == 401:  # bad or expired token
                headers = self.set_request_headers()
                response = requests.get(uri, headers=headers)

            content = response.json()

            for track in content['items']:
                track_uris.append(track['uri'])

        print('Retrieved tracks!')
        return track_uris

    """Create a new playlist in user's account"""
    def create_playlist(self, user_id=USER_ID):

        current_date = (date.today()).strftime('%m-%d-%Y')
        playlist_name = f'New Monthly Releases - {current_date}'

        uri = f'https://api.spotify.com/v1/users/{user_id}/playlists'
        payload = {'name': playlist_name}
        response = requests.post(uri, headers=self.set_request_headers(), data=json.dumps(payload))
        content = response.json()

        session['playlist_id'] = content['id']  # store our new playlist's id
        session['playlist_url'] = content['external_urls']['spotify']  # store new playlist's url

        print(f'{response.status_code} - Created playlist!')
        return session['playlist_id']

    """Add new music releases to our newly created playlist"""
    def add_to_playlist(self, track_uris):

        headers = self.set_request_headers()
        playlist_id = self.create_playlist()
        number_of_tracks = len(track_uris)  # Spotify API limit - max 100 tracks per req.

        if number_of_tracks > 200:
            three_split = np.array_split(track_uris, 3)
            for lst in three_split:
                hp.add_tracks(headers, playlist_id, list(lst))

        elif number_of_tracks > 100:
            two_split = np.array_split(track_uris, 2)
            for lst in two_split:
                hp.add_tracks(headers, playlist_id, list(lst))

        else:
            hp.add_tracks(headers, playlist_id, track_uris)

        print('Added tracks to playlist!')
        hp.shutdown_server(request.environ)
        return session['playlist_url']
