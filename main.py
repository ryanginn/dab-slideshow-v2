from PIL import Image, ImageDraw, ImageFont, ImageFilter
import requests
from io import BytesIO
import configparser
import time
import copy
import os
import urllib.parse # Import for URL encoding

# Debugging function
def debug_print(message):
    print(f"DEBUG: {message}")

# Read the configuration file
config = configparser.ConfigParser()
config.read('dab-broadcast.conf')

# Configuration variables
icecast_url = config.get('dab-broadcast', 'icecast_url')
azuracast_url = config.get('dab-broadcast', 'azuracast_url')
use_icecast = config.getboolean('dab-broadcast', 'use_icecast')
output_image_path = config.get('dab-broadcast', 'output_image')
artist_font_path = config.get('dab-broadcast', 'artist_font')
title_font_path = config.get('dab-broadcast', 'title_font')
# --- NEW: Last.fm API Key ---
LASTFM_API_KEY = config.get('dab-broadcast', 'lastfm_api_key', fallback='')
# ---------------------------
LOGO_FILE_PATH = 'logo.png' # Specified logo file

# Define the target image size (REQUIRED: 320x240)
TARGET_WIDTH = 320
TARGET_HEIGHT = 240
LOGO_BLOCK_SIZE = 55 # New uniform logo size (55x55)
TEXT_BG_HEIGHT = 55
TEXT_BG_Y = TARGET_HEIGHT - TEXT_BG_HEIGHT

# --- INITIAL FONT CONFIGURATION ---
INITIAL_FONT_SIZE = 20
try:
    base_font = ImageFont.truetype(title_font_path, INITIAL_FONT_SIZE)
    # logo_font no longer needed as we are using an image
    debug_print("Fonts loaded successfully.")
except Exception as e:
    print(f"Error loading fonts: {e}")
    exit(1)

# Initialize variables
last_songname = ""

# Function to load and resize the logo (Updated to 55x55)
def load_logo(path, target_size=(LOGO_BLOCK_SIZE, LOGO_BLOCK_SIZE)):
    if not os.path.exists(path):
        debug_print(f"Logo file not found at: {path}")
        return None
    try:
        # Load logo with a default mode if it's not a standard PNG/JPEG type
        logo = Image.open(path)
        if logo.mode != "RGBA":
            logo = logo.convert("RGBA")

        logo = logo.resize(target_size, Image.LANCZOS)
        debug_print(f"Logo loaded and resized to {target_size}.")
        return logo
    except Exception as e:
        debug_print(f"Error loading logo: {e}")
        return None
    
# --- Load Logo for Fallback Use (Added global variable to hold the full-size logo for fallback) ---
# We load the full-size logo now and save it to a variable for later use.
try:
    FALLBACK_LOGO_FULL = Image.open(LOGO_FILE_PATH).convert("RGBA")
    debug_print("Fallback logo (full size) loaded successfully.")
except Exception as e:
    FALLBACK_LOGO_FULL = None
    print(f"Warning: Could not load logo for fallback use: {e}")
# ------------------------------------------------------------------------------------------------

# --- NEW FUNCTION: Fetch album art URL from Last.fm ---
def fetch_lastfm_album_art_url(artistname, songname, api_key):
    if not artistname or not songname or not api_key:
        debug_print("Missing artist, song, or Last.fm API key.")
        return None

    LASTFM_API_URL = "http://ws.audioscrobbler.com/2.0/"

    params = {
        'method': 'track.getInfo',
        'api_key': api_key,
        'artist': artistname,
        'track': songname,
        'format': 'json',
        'autocorrect': 1 # To help find the right track
    }

    try:
        debug_print(f"Last.fm lookup for: {artistname} - {songname}")
        response = requests.get(LASTFM_API_URL, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            # The album art URL is nested in the response: track -> album -> image

            # Check for error first
            if 'error' in data:
                debug_print(f"Last.fm API Error: {data.get('message', 'Unknown Error')}")
                return None

            album_images = data.get("track", {}).get("album", {}).get("image", [])

            for image in album_images:
                if image.get("size") == "extralarge":
                    url = image.get("#text")
                    if url and not url.endswith('2a96cbd8b46e442fc41c2b86b821562f'): # Check for "no image" last.fm url
                        debug_print(f"Last.fm Album Art URL found: {url}")
                        return url
                    elif url and url.endswith('2a96cbd8b46e442fc41c2b86b821562f'):
                        debug_print("Last.fm returned 'no image' URL.")

            # Fallback for large size if extralarge is not found
            for image in album_images:
                if image.get("size") == "large":
                    url = image.get("#text")
                    if url and not url.endswith('2a96cbd8b46e442fc41c2b86b821562f'):
                        debug_print(f"Last.fm Album Art URL found (large fallback): {url}")
                        return url

            debug_print("Last.fm found song but no suitable album art URL.")
            return None

        else:
            debug_print(f"Last.fm HTTP Error: {response.status_code}")
            return None

    except requests.exceptions.RequestException as e:
        debug_print(f"Last.fm Request Error: {e}")
        return None
# -------------------------------------------------------------------


# Function to adjust font size based on the text length
def adjust_font_size(text, font, max_width):
    # This block is unchanged, kept for completeness
    image = Image.new('RGBA', (max_width, 1))
    draw = ImageDraw.Draw(image)
    current_font = copy.copy(font)

    try:
        bbox = draw.textbbox((0, 0), text, font=current_font)
        width = bbox[2] - bbox[0]
    except Exception as e:
        debug_print(f"Initial font measurement error for '{text}': {e}. Returning initial size.")
        return current_font

    while width > max_width and current_font.size > 1:
        new_size = current_font.size - 1
        current_font = ImageFont.truetype(current_font.path, new_size)

        try:
            bbox = draw.textbbox((0, 0), text, font=current_font)
            width = bbox[2] - bbox[0]
        except Exception:
            break

    return current_font

# Function to truncate the text with ellipsis if it's too long
def truncate_text(text, font, max_width):
    # This block is unchanged, kept for completeness
    image = Image.new('RGBA', (max_width, 1))
    draw = ImageDraw.Draw(image)

    original_text = text
    while draw.textbbox((0, 0), text, font=font)[2] > max_width and len(text) > 3:
        text = text[:-4] + "..."

        if len(text) <= 3 and len(original_text) > 3:
            text = original_text[:3] + "..."
            break

    return text

# Function to fetch now playing data with retries
def fetch_now_playing_with_retries(max_retries=3, retry_delay=5):
    global LASTFM_API_KEY # Use the global variable
    for attempt in range(max_retries):
        try:
            now_playing_url = icecast_url if use_icecast else azuracast_url
            debug_print(f"Fetching data from: {now_playing_url}, Attempt: {attempt + 1}")
            response = requests.get(now_playing_url, timeout=10)

            if response.status_code != 200:
                print(f"Error: Received response with status code {response.status_code}")
                return None, None, None

            try:
                data = response.json()
                debug_print("Data fetched successfully.")
            except ValueError:
                print("Error: Received non-JSON response")
                return None, None, None

            # Parse song and artist name
            songname = ""
            artistname = ""
            album_art_url = None

            if use_icecast:
                full_title = data.get("icestats", {}).get("source", {}).get("title", "")
                if "-" in full_title:
                    artistname, songname = map(str.strip, full_title.split("-", 1))
                else:
                    songname = full_title

                # --- MODIFIED: Last.fm lookup for Icecast Album Art ---
                if LASTFM_API_KEY and artistname and songname:
                    album_art_url = fetch_lastfm_album_art_url(artistname, songname, LASTFM_API_KEY)
                # ----------------------------------------------------

            else: # Azuracast
                song_data = data.get("now_playing", {}).get("song", {})
                songname = song_data.get("title", "").split("(")[0].strip()
                artistname = song_data.get("artist", "")
                album_art_url = data.get("now_playing", {}).get("song", {}).get("art", None)

            debug_print(f"Now playing: {songname} by {artistname}")
            return songname, artistname, album_art_url

        except Exception as e:
            debug_print(f"Error on attempt {attempt + 1}: {e}")
            time.sleep(retry_delay)

    debug_print(f"Failed to fetch now-playing data after {max_retries} retries.")
    return None, None, None

# Function to fetch album art
def fetch_album_art(album_art_url):
    # This block is unchanged, kept for completeness
    try:
        if not album_art_url:
             debug_print("Album art URL is empty. Returning None.")
             return None

        debug_print(f"Fetching album art from: {album_art_url}")
        art_response = requests.get(album_art_url, timeout=10)
        if art_response.status_code == 200:
            album_art = Image.open(BytesIO(art_response.content))
            album_art = album_art.convert("RGBA")
            debug_print("Album art fetched successfully.")
            return album_art
        else:
            debug_print(f"Error fetching album art: {art_response.status_code}")
            return None
    except Exception as e:
        debug_print(f"Error fetching album art: {e}")
        return None

# Main loop to keep updating the image
while True:
    try:
        songname, artistname, album_art_url = fetch_now_playing_with_retries(max_retries=5, retry_delay=10)

        if songname and (songname != last_songname or not last_songname):
            debug_print(f"Song has changed or is initial run: {last_songname} -> {songname}")

            # Create a blank image with the target dimensions (320x240)
            output_image = Image.new("RGBA", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0, 255))
            draw = ImageDraw.Draw(output_image)
            
            # --- Album Art and Fallback Logic ---
            
            # Attempt to fetch album art
            album_art_full_res = None
            if album_art_url:
                album_art_full_res = fetch_album_art(album_art_url)

            # Use fallback logo if album art is not found
            if album_art_full_res is None and FALLBACK_LOGO_FULL is not None:
                debug_print("Using fallback logo as album art.")
                album_art_full_res = FALLBACK_LOGO_FULL
            elif album_art_full_res is None:
                debug_print("No album art and no fallback logo available.")

            # --- Background: Blurred Album Art/Fallback Logo ---
            if album_art_full_res:
                img_ratio = album_art_full_res.width / album_art_full_res.height
                output_ratio = TARGET_WIDTH / TARGET_HEIGHT

                if img_ratio > output_ratio:
                    new_height = TARGET_HEIGHT
                    new_width = int(new_height * img_ratio)
                    resized_art = album_art_full_res.resize((new_width, new_height), Image.LANCZOS)
                    left = (new_width - TARGET_WIDTH) / 2
                    resized_art = resized_art.crop((left, 0, left + TARGET_WIDTH, TARGET_HEIGHT))
                else:
                    new_width = TARGET_WIDTH
                    new_height = int(new_width / img_ratio)
                    resized_art = album_art_full_res.resize((new_width, new_height), Image.LANCZOS)
                    top = (new_height - TARGET_HEIGHT) / 2
                    resized_art = resized_art.crop((0, top, TARGET_WIDTH, top + TARGET_HEIGHT))

                blurred_background = resized_art.filter(ImageFilter.GaussianBlur(radius=8))
                output_image.paste(blurred_background, (0, 0))
            else:
                debug_print("Could not create blurred background (using black default).")

            # --- Album Art Thumbnail and Border (Position based on text bar) ---
            album_art_thumbnail_size = 140
            border_size = 2

            # Position calculations
            available_top_space = TEXT_BG_Y - 0
            thumb_x = (TARGET_WIDTH - album_art_thumbnail_size) // 2
            thumb_y = (available_top_space - album_art_thumbnail_size) // 2

            # Draw Border (a grey rectangle)
            border_rect = (
                thumb_x - border_size,
                thumb_y - border_size,
                thumb_x + album_art_thumbnail_size + border_size,
                thumb_y + album_art_thumbnail_size + border_size
            )
            draw.rectangle(border_rect, fill=(55, 56, 52, 180))

            if album_art_full_res: # Use the same image (either fetched or fallback) for the thumbnail
                album_art_thumbnail = album_art_full_res.resize((album_art_thumbnail_size, album_art_thumbnail_size), Image.LANCZOS)
                output_image.paste(album_art_thumbnail, (thumb_x, thumb_y), album_art_thumbnail)
            else:
                debug_print("Could not create album art thumbnail (only border visible).")

            # --- Text Overlay and Logo Placement ---

            overlay = Image.new('RGBA', (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)

            # Draw the dark bottom bar
            overlay_draw.rectangle([(0, TEXT_BG_Y), (TARGET_WIDTH, TARGET_HEIGHT)], fill=(0, 0, 0, 180))

            # --- Load and Paste External Logo (No pink background) ---
            logo_img = load_logo(LOGO_FILE_PATH)
            if logo_img:
                # Logo position is (0, TEXT_BG_Y) which is the top-left of the logo block area
                logo_x = 0
                logo_y = TEXT_BG_Y
                # Paste the logo directly onto the dark bottom bar
                overlay.paste(logo_img, (logo_x, logo_y), logo_img)


            output_image = Image.alpha_composite(output_image, overlay)
            draw = ImageDraw.Draw(output_image)

            # --- Text Uniform Font Size Calculation and Positioning ---

            TEXT_LEFT_PADDING = 10 # Padding from the logo block
            # Text starts to the right of the 55px logo block
            TEXT_BLOCK_LEFT_EDGE = LOGO_BLOCK_SIZE + TEXT_LEFT_PADDING
            TEXT_BLOCK_RIGHT_EDGE = TARGET_WIDTH - 5
            max_text_width = TEXT_BLOCK_RIGHT_EDGE - TEXT_BLOCK_LEFT_EDGE

            # 1. Calculate the required font size for Artist and Title independently
            artist_font_adjusted = adjust_font_size(artistname, base_font, max_text_width)
            title_font_adjusted = adjust_font_size(songname, base_font, max_text_width)

            # 2. Find the minimum size and create a new, final uniform font object
            final_size = min(artist_font_adjusted.size, title_font_adjusted.size)
            final_font = ImageFont.truetype(base_font.path, final_size)

            # Truncate text using the final uniform font size
            artistname_display = artistname.upper() # Uppercase for "bold" effect
            artistname_display = truncate_text(artistname_display, final_font, max_text_width)
            songname_display = truncate_text(songname, final_font, max_text_width)

            # Positioning: Text is Left Aligned (X coordinate is TEXT_BLOCK_LEFT_EDGE)
            text_x = TEXT_BLOCK_LEFT_EDGE

            # Artist (Top Line) Y-Position (middle of the line)
            artist_y_offset = 19
            # Title (Bottom Line) Y-Position (middle of the line)
            title_y_offset = 38

            artistname_position = (text_x, TEXT_BG_Y + artist_y_offset)
            songname_position = (text_x, TEXT_BG_Y + title_y_offset)

            # Draw Text with stroke/shadow - Use anchor="lm" (Left Middle) for left alignment
            stroke_width = 1
            stroke_fill = (0, 0, 0, 150)

            # Draw Artist (Top Line) - BOLD/UPPERCASE
            draw.text(artistname_position, artistname_display, fill="white", font=final_font, anchor="lm", stroke_width=stroke_width, stroke_fill=stroke_fill)

            # Draw Title (Bottom Line)
            draw.text(songname_position, songname_display, fill="white", font=final_font, anchor="lm", stroke_width=stroke_width, stroke_fill=stroke_fill)


            # Convert image for JPEG if needed
            if output_image_path.lower().endswith('.jpg') or output_image_path.lower().endswith('.jpeg'):
                output_image = output_image.convert("RGB")

            # Save image and update last song
            output_image.save(output_image_path)
            debug_print(f"Image saved to {output_image_path}")
            last_songname = songname

        else:
            debug_print("Song has not changed, skipping image update.")

        # Wait before refreshing
        time.sleep(10)

    except Exception as e:
        debug_print(f"Error in main loop: {e}")
        time.sleep(10)