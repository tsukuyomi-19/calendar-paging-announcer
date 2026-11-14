import os
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
import sys
import traceback
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pickle
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    WebDriverException,
    NoSuchElementException
)
import requests
import json
import platform
import subprocess
from webdriver_manager.chrome import ChromeDriverManager
from functools import wraps
from typing import List, Tuple, Dict, Optional, Any, Callable
import socket
import gc
import signal
from flask import Flask, request, jsonify
import threading

# Configuration constants
LOGIN = os.environ.get("PAGING_LOGIN", "")
PASSWORD = os.environ.get("PAGING_PASSWORD", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "")  # ElevenLabs voice ID
PAGING_URL = os.environ.get("PAGING_URL", "https://paging.example.local:8088")
    
# Shutdown time - Change SHUTDOWN_HOUR if needed
SHUTDOWN_HOUR = 21
SHUTDOWN_MINUTE = 0

# Email configuration
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")

# PGroup Configuration
HIGHSCHOOL_PGROUP = "40"
HIGHSCHOOL_AND_SIXTH_PGROUP = "43"
HIGHSCHOOL_SIXTH_AND_FIFTH_PGROUP = "40"

# 5th grade configuration - Before each season, if there are fifth graders
# on the team, change the value for that team to true:
BASKETBALL_MS_BOYS_HAS_FIFTH_GRADERS = False
BASKETBALL_MS_GIRLS_HAS_FIFTH_GRADERS = False
SOCCER_MS_BOYS_HAS_FIFTH_GRADERS = False
SOCCER_MS_GIRLS_HAS_FIFTH_GRADERS = False
BASEBALL_MS_BOYS_HAS_FIFTH_GRADERS = False
VOLLEYBALL_MS_GIRLS_HAS_FIFTH_GRADERS = False


# Define recipient email groups
def _email_list(var: str) -> list:
    return [e.strip() for e in os.environ.get(var, "").split(",") if e.strip()]

SECRETARY_EMAILS = _email_list("SECRETARY_EMAILS")
IT_EMAILS = _email_list("IT_EMAILS")
ANNOUNCEMENT_FAILURE_EMAILS = SECRETARY_EMAILS + IT_EMAILS

CALENDAR_IDS = [
    'Baseball - V (Boys)',
    'Baseball - JV (Boys)',
    'Baseball - MS (Boys)',
    'Basketball - V (Boys)',
    'Basketball - V (Girls)',
    'Basketball - JV (Boys)',
    'Basketball - JV (Girls)',
    'Basketball - MS (Boys)',
    'Basketball - MS (Girls)',
    'Cross-Country - V (Coed)',
    'Flag Football - JV (Coed)',
    'Soccer - MS (Boys)',
    'Soccer - MS (Girls)',
    'Soccer - JV (Boys)',
    'Soccer - V (Boys)',
    'Soccer - V (Girls)',
    'Softball V (Girls)',
    'Volleyball - JV (Girls)',
    'Volleyball - MS (Girls)',
    'Volleyball - V (Girls)'
]

# Google Calendar API configuration
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, 'credentials.json')
TOKEN_FILE = os.path.join(SCRIPT_DIR, 'token.pickle')

app = Flask(__name__)
_system_instance_for_signal = None

def get_est_now():
    """Get current time in Eastern Time, handling DST correctly"""
    return datetime.now(ZoneInfo("America/New_York"))

def clean_time_string(time_str: str) -> Optional[str]:
    """
    Clean and standardize time string format, making it more forgiving for various inputs.
    Examples: "1:45pm", "1.45 PM", "1-45p.m." -> "1:45 pm"
    """
    if not time_str:
        return None
        
    # Convert to lowercase and remove extra spaces
    cleaned = time_str.lower().strip()
    
    # Handle special cases
    if cleaned == "noon":
        return "12:00 pm"
    if cleaned == "midnight":
        return "12:00 am"
        
    # Remove all punctuation except digits
    cleaned = re.sub(r'[^0-9amp]', '', cleaned)
    
    # Extract hours and minutes using regex
    time_match = re.search(r'(\d{1,2})(?:\d{2})|(\d{1,2})', cleaned)
    if not time_match:
        return None
        
    hours = time_match.group(1) or time_match.group(2)
    hours = int(hours)
    
    # Extract minutes if present
    minutes_match = re.search(r'\d{1,2}(\d{2})', cleaned)
    minutes = minutes_match.group(1) if minutes_match else "00"
    
    # Determine AM/PM
    is_am = 'a' in cleaned
    is_pm = 'p' in cleaned or not (is_am)  # Default to PM if not specified
    
    # Format the final time string
    period = "am" if is_am else "pm"
    return f"{hours}:{minutes} {period}"

def extract_team_names(event_title: str) -> str:
    """
    Extract team names from event title, typically in format "Team1 @ Team2"
    Returns only the first team (Team1)
    """
    if not event_title:
        return ""
        
    # Split on '@' and take first part
    parts = event_title.split('@')[0].strip()
    
    # Handle special cases (MS -> Middle School)
    return parts.replace('MS', 'Middle School')

def format_team_announcement(team_names: List[str]) -> str:
    """
    Format team names for announcement following Oxford comma rules
    """
    if not team_names:
        return ""
    
    if len(team_names) == 1:
        return f"{team_names[0]} is now dismissed"
    elif len(team_names) == 2:
        return f"{team_names[0]} and {team_names[1]} are now dismissed"
    else:
        *first_teams, last_team = team_names
        teams_text = ", ".join(first_teams)
        return f"{teams_text}, and {last_team} are now dismissed"

def process_calendar_events(events: List[dict]) -> List[Tuple[str, List[str]]]:
    """
    Process multiple calendar events and group them by dismiss time
    Returns: List of tuples (dismiss_time, [team_names])
    """
    # Group events by dismiss time
    time_groups = {}
    
    for event in events:
        title = event.get('summary', '')
        description = event.get('description', '')
        
        # Skip cancelled or postponed events
        if any(word in title.lower() for word in ['cancel', 'cancelled', 'canceled', 'postponed']):
            continue
            
        # Extract and clean dismiss time
        logging.debug(f"Checking event: {event.get('summary')}, Description: {event.get('description')}")
        time_match = re.search(r"Dismiss\s+(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|noon|midnight)", 
                             description, 
                             re.IGNORECASE)
        if not time_match:
            continue
            
        dismiss_time = clean_time_string(time_match.group(1))
        if not dismiss_time:
            continue
            
        # Extract team name
        team_name = extract_team_names(title)
        
        # Group by dismiss time
        if dismiss_time not in time_groups:
            time_groups[dismiss_time] = []
        time_groups[dismiss_time].append(team_name)
    
    # Format announcements for each time group
    announcements = []
    for time, teams in time_groups.items():
        announcements.append((time, teams))
    
    return announcements

def retry_decorator(max_attempts: int = 3, delay: int = 10, escalate_to_email: bool = True):
    """
    Enhanced retry decorator that can escalate to email notification
    after all retry attempts have failed
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            last_error = None
            for attempt in range(max_attempts):
                try:
                    logging.debug(f"Attempting {func.__name__} (attempt {attempt + 1}/{max_attempts})")
                    return func(self, *args, **kwargs)
                except Exception as e:
                    last_error = e
                    error_details = traceback.format_exc()
                    logging.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {str(e)}\n{error_details}")
                    
                    if attempt == max_attempts - 1:
                        logging.error(f"Function {func.__name__} failed after {max_attempts} attempts: {str(e)}")
                        # If final attempt and email notification is enabled
                        if escalate_to_email and hasattr(self, 'send_error_notification'):
                            function_args = f"Args: {args}, Kwargs: {kwargs}"
                            self.send_error_notification(
                                subject=f"Paging System - Critical Error in {func.__name__}",
                                message=(
                                    f"Function {func.__name__} failed after {max_attempts} attempts.\n\n"
                                    f"Error: {str(last_error)}\n\n"
                                    f"Function call details: {function_args}\n\n"
                                    f"Stack Trace:\n{error_details}"
                                ),
                                recipients=IT_EMAILS
                            )
                        raise
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

def internet_connection_decorator(func):
    """
    Decorator to verify internet connection before executing functions
    that require network access
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # Try to connect to Google's DNS to verify internet connection
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return func(self, *args, **kwargs)
        except (socket.error, socket.timeout) as e:
            error_msg = f"No internet connection available: {str(e)}"
            logging.error(error_msg)
            self.send_error_notification(
                                subject=f"Paging System - Internet Connection Error",
                                message=(
                                    f"Function {func.__name__} could not be executed due to network issues.\n\n"
                                    f"Error: {error_msg}\n\n"
                                    f"The paging system will attempt to retry at the next scheduled interval\n"
                                    f"Please ensure the server has stable internet connectivity"
                                ),
                                recipients=IT_EMAILS
                            )
            raise ConnectionError(error_msg)
    return wrapper

class IntegratedPagingSystem:
    def __init__(self):
        self.setup_logging()
        self.calendar_service = None
        self.last_processed_events = set()
        self.last_announcement_time = None
        self.last_error_notification_time = None
        self.health_check_last_run = None
        self.shutdown_requested = False
        self.active_driver = None
        
    def setup_logging(self):
        """Configure logging with rotation and more detailed formatting"""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        log_file = os.path.join(script_dir, 'integrated_paging.log')
        
        log_format = "%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
        
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(log_format))
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(log_format))
        
        logging.root.setLevel(logging.DEBUG)
        logging.root.handlers = []
        logging.root.addHandler(file_handler)
        logging.root.addHandler(console_handler)
        
        logging.info("Logging system initialized from Integrated Paging System")

    def send_error_notification(self, subject: str, message: str, recipients: Optional[List[str]] = None):
        """
        Send email notification for errors.
        If recipients is None, defaults to IT_EMAILS.
        Includes rate limiting to prevent flooding inboxes.
        """
        now = get_est_now()

        # Use default IT recipients if none are specified
        if recipients is None:
            recipients = IT_EMAILS # Default to IT

        if not recipients: # If after defaulting, it's still empty or None
            logging.warning("No recipients specified for error notification. Skipping email.")
            return False

        if self.last_error_notification_time and \
           (now - self.last_error_notification_time) < timedelta(minutes=30): # Using existing global rate limit
            logging.info(f"Error notification (to {', '.join(recipients)}) rate limited - skipping email")
            return False
            
        try:
            logging.info(f"Sending error notification to {', '.join(recipients)} with subject: {subject}")
            
            msg = MIMEMultipart()
            msg['From'] = EMAIL_USER 
            msg['To'] = ", ".join(recipients) # Use the passed-in recipients
            msg['Subject'] = f"PAGING SYSTEM ALERT: {subject}"
            
            body = f"Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n" # Added %Z for timezone
            body += f"Host: {socket.gethostname()}\n\n"
            body += message
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                # server.set_debuglevel(1) # Enable for testing SMTP issues
                logging.debug(f"Connecting to SMTP server: {SMTP_SERVER}:{SMTP_PORT}")
                server.ehlo() 
                server.starttls()
                server.ehlo() 
                logging.debug("TLS started. Logging in...")
                server.login(EMAIL_USER, EMAIL_PASSWORD) 
                logging.debug(f"Logged in as {EMAIL_USER}. Sending message to {', '.join(recipients)}...")
                server.send_message(msg)
                logging.debug("Message sent.")
                
            # Only update last_error_notification_time if an email was actually attempted to this group
            # For global rate limit, this is fine.
            self.last_error_notification_time = now 
            logging.info("Error notification sent successfully")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logging.error(f"SMTP Authentication Error for {EMAIL_USER}: {str(e)}. Check credentials and App Password settings.")
            return False
        except smtplib.SMTPConnectError as e:
            logging.error(f"SMTP Connection Error: Could not connect to {SMTP_SERVER}:{SMTP_PORT}. {str(e)}")
            return False
        except smtplib.SMTPServerDisconnected as e:
            logging.error(f"SMTP Server Disconnected: {str(e)}")
            return False
        except socket.gaierror as e: 
            logging.error(f"SMTP DNS Resolution Error for {SMTP_SERVER}: {str(e)}")
            return False
        except socket.timeout as e: 
            logging.error(f"SMTP Connection Timeout for {SMTP_SERVER}:{SMTP_PORT}: {str(e)}")
            return False
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Failed to send error notification to {', '.join(recipients)}: {str(e)}\n{error_details}")
            return False
            
    def get_team_config_key(self, team_name: str, calendar_name: str) -> str:
        """Helper to generate a consistent key for team config booleans."""
        # Normalize team name for key generation (uppercase, replace spaces/hyphens with underscores)
        # Also consider stripping level indicators like (Boys), (Girls), (Coed) if the booleans are general for the sport

        key_part = calendar_name.upper().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "")

        return f"{key_part}_HAS_FIFTH_GRADERS"

    def determine_pgroup_for_teams(self, teams_in_announcement: List[str], event_calendar_names: List[str]) -> str:
        """
        Determines the correct paging group based on team names and 5th grader status.
        'teams_in_announcement' are the processed names like "Middle School Soccer".
        'event_calendar_names' are the original calendar names like "Soccer - MS (Boys)".
        """
        # Check for 5th grader override first (HIGHSCHOOL_SIXTH_AND_FIFTH_PGROUP)
        # This rule takes precedence and applies if ANY team in the announcement has 5th graders.
        for calendar_name in event_calendar_names: # Iterate over original calendar names for this specific announcement
            base_config_name = self.get_team_config_key(None, calendar_name)
            fifth_grader_config_key = f"{base_config_name}_HAS_FIFTH_GRADERS"

            if fifth_grader_config_key in globals() and globals()[fifth_grader_config_key] is True:
                logging.info(f"Calendar '{calendar_name}' (config key {fifth_grader_config_key}) indicates 5th graders. Using pgroup {HIGHSCHOOL_SIXTH_AND_FIFTH_PGROUP}.")
                return HIGHSCHOOL_SIXTH_AND_FIFTH_PGROUP
                
        use_pgroup_special_teams = False
        # Check based on processed team names (e.g., "Middle School Soccer")
        for team_name in teams_in_announcement:
            team_name_lower = team_name.lower()
            if "cross-country" in team_name_lower or \
               "flag football" in team_name_lower or \
               "middle school" in team_name_lower:
                use_pgroup_special_teams = True
                break

        # Fallback check based on original calendar names if needed (more robust)
        if not use_pgroup_special_teams:
            for calendar_name in event_calendar_names:
                calendar_name_lower = calendar_name.lower()
                if "cross-country" in calendar_name_lower or \
                   "flag football" in calendar_name_lower or \
                   "ms" in calendar_name_lower: # Check for "ms" for Middle School
                    use_pgroup_special_teams = True
                    break

        if use_pgroup_special_teams:
            logging.info(f"Teams for announcement ({teams_in_announcement} from calendars {event_calendar_names}) match criteria for MS/CC/FF. Using pgroup {HIGHSCHOOL_AND_SIXTH_PGROUP}.")
            return HIGHSCHOOL_AND_SIXTH_PGROUP # "43"

        logging.info(f"Teams for announcement ({teams_in_announcement} from calendars {event_calendar_names}) do not match special criteria. Using default pgroup {HIGHSCHOOL_PGROUP}.")
        return HIGHSCHOOL_PGROUP # "40"

    @internet_connection_decorator
    @retry_decorator(max_attempts=3, delay=30)
    def authenticate_calendar(self):
        """Handle Google Calendar authentication with retry logic"""
        logging.info("Starting Google Calendar authentication process")
        creds = None
        if os.path.exists(TOKEN_FILE):
            logging.debug("Found existing token file")
            try:
                # Check file size before attempting to load
                if os.path.getsize(TOKEN_FILE) < 10:  # Too small to be valid
                    logging.warning("Token file appears corrupted (too small)")
                    os.remove(TOKEN_FILE)
                    creds = None
                else:
                    with open(TOKEN_FILE, 'rb') as token:
                        creds = pickle.load(token)
            except (pickle.PickleError, EOFError) as e:
                logging.error(f"Error loading token file: {str(e)}")
                # If token file is corrupted, remove it
                os.remove(TOKEN_FILE)
                creds = None

        if not creds or not creds.valid:
            logging.info("Credentials invalid or expired, refreshing...")
            if creds and creds.expired and creds.refresh_token:
                try:
                    logging.debug("Refreshing expired credentials")
                    creds.refresh(Request())
                except Exception as e:
                    logging.error(f"Failed to refresh token: {str(e)}")
                    # If refresh fails, create new credentials
                    creds = None
            
            if not creds:
                logging.info("Initiating OAuth2 flow for new credentials")
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                    creds = flow.run_local_server(port=0)
                except FileNotFoundError:
                    error_msg = f"Credentials file not found: {CREDENTIALS_FILE}"
                    logging.critical(error_msg)
                    self.send_error_notification(
                        subject="PAGING ALERT: CRITICAL - Google Calendar Credentials Missing", # Emphasized subject
                        message=(
                            f"The Google Calendar credentials file ('{os.path.basename(CREDENTIALS_FILE)}') was not found at the expected location: {CREDENTIALS_FILE}\n\n"
                            f"The paging system cannot authenticate with Google Calendar and will not be able to fetch events.\n\n"
                            f"ACTION REQUIRED: Please ensure the '{os.path.basename(CREDENTIALS_FILE)}' file is correctly placed in the script directory."
                        ),
                        recipients=IT_EMAILS # Explicitly to IT
                    )
                    raise
                    
            logging.debug("Saving new credentials to token file")
            try:
                with open(TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)
            except Exception as e:
                logging.error(f"Failed to save token: {str(e)}")
                # Continue anyway - we have valid credentials in memory

        try:
            self.calendar_service = build('calendar', 'v3', credentials=creds)
            logging.info("Successfully authenticated with Google Calendar")
        except Exception as e:
            error_msg = f"Failed to create calendar service: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg)

    # No retry decorator to avoid accidental repeats of the announcement
    def speak_announcement(self, announcement: str, repeat_count: int = 2) -> bool: # Added repeat_count
        """Generate and play announcement using ElevenLabs TTS, optionally repeating."""
        logging.info(f"Preparing to generate speech for announcement: {announcement} (will play {repeat_count} times)")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        temp_file = os.path.join(script_dir, "temp_announcement.mp3")
        
        # --- TTS Generation (occurs once) ---
        try:            
            logging.debug("Generating speech audio via ElevenLabs API")
            # ... (ElevenLabs API call and save to temp_file as before) ...
            # (Ensure this part is successful before attempting to play multiple times)
            # Example structure:
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": ELEVENLABS_API_KEY
            }
            data = {
                "text": announcement,
                "model_id": "eleven_multilingual_v2", # Or your preferred model
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.5
                }
            }
            api_url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
            
            response = requests.post(api_url, json=data, headers=headers)
            if response.status_code != 200:
                logging.error(f"ElevenLabs API request failed with status {response.status_code}: {response.text}")
                raise requests.RequestException(f"ElevenLabs API request failed: {response.status_code}")

            with open(temp_file, 'wb') as f:
                f.write(response.content)
            logging.debug(f"Audio saved to temporary file: {temp_file}")

            # --- Audio Playback (repeated) ---
            system = platform.system()
            playback_successful_overall = True # Track overall success

            for i in range(repeat_count):
                logging.info(f"Playing announcement (repetition {i + 1}/{repeat_count})...")
                current_repetition_successful = False
                try:
                    if system == "Windows":
                        # Using the simplified PowerShell approach that was working for you:
                        ps_path = os.path.abspath(temp_file).replace("'", "''")
                        max_playback_seconds = 180 # Or make this configurable

                        command = (
                            "$ErrorActionPreference = 'Stop'; "
                            "Add-Type -AssemblyName PresentationCore; "
                            "$player = New-Object System.Windows.Media.MediaPlayer; "
                            "$player.Volume = 1.0;"
                            f"$uri = New-Object System.Uri('{ps_path}', [System.UriKind]::Absolute); "
                            "$player.Open($uri); "
                            "Start-Sleep -Milliseconds 500; " # Brief pause for Open/NaturalDuration
                            "if ($player.NaturalDuration.HasTimeSpan) { "
                            "    $player.Play(); "
                            "    Write-Host ('MediaPlayer: Play command issued. Expected duration: ' + $player.NaturalDuration.TimeSpan); "
                            "    $watch = [System.Diagnostics.Stopwatch]::StartNew(); "
                            "    $timeout = [TimeSpan]::FromSeconds(" + str(max_playback_seconds) + "); "
                            "    while ($player.Source -ne $null -and $player.Position -lt $player.NaturalDuration.TimeSpan -and $watch.Elapsed -lt $timeout) { "
                            "        Start-Sleep -Milliseconds 100; "
                            "    } "
                            "    $watch.Stop(); "
                            "    if ($watch.Elapsed -ge $timeout) { Write-Warning 'MediaPlayer playback timed out.'; } "
                            "    else { Write-Host 'MediaPlayer playback likely completed.'; } "
                            "} else { "
                            "    $player.Play(); "
                            "    Write-Warning 'MediaPlayer: NaturalDuration not available. Playing for a fixed fallback duration (30s).'; "
                            "    Start-Sleep -Seconds 30; " # Fallback duration
                            "} "
                            "$player.Close(); "
                            "if ($player -is [System.__ComObject]) { $null = [System.Runtime.InteropServices.Marshal]::ReleaseComObject($player); } "
                            "Remove-Variable player, uri, watch, timeout -ErrorAction SilentlyContinue; "
                        )
                        
                        logging.debug(f"Executing PowerShell command (repetition {i+1}): {command}")
                        result = subprocess.run(
                            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, shell=False, check=False
                        )

                        if result.returncode == 0 and not ("playback timed out" in result.stdout or "playback timed out" in result.stderr): # Basic success check
                            logging.info(f"PowerShell playback command (repetition {i+1}) executed successfully. Stdout: {result.stdout.strip()}")
                            current_repetition_successful = True
                        else:
                            logging.error(f"PowerShell playback command (repetition {i+1}) failed or timed out. Exit code: {result.returncode}")
                            if result.stderr: logging.error(f"PowerShell Stderr: {result.stderr.strip()}")
                            if result.stdout: logging.info(f"PowerShell Stdout: {result.stdout.strip()}")
                            # Decide if one failure should stop all repetitions
                            playback_successful_overall = False 
                            break # Stop repeating if one playback fails

                    elif system == "Darwin": # macOS
                        # ... (your afplay logic, ensure it waits for completion if repeating) ...
                        # For afplay, it blocks by default, so a simple loop is fine.
                        result = subprocess.run(["afplay", temp_file], capture_output=True, text=True)
                        if result.returncode == 0:
                            logging.info(f"afplay command (repetition {i+1}) executed successfully.")
                            current_repetition_successful = True
                        else:
                            logging.error(f"afplay failed (repetition {i+1}). Code: {result.returncode}, Error: {result.stderr.strip()}")
                            playback_successful_overall = False
                            break 
                            
                    else:  # Linux and others
                        # ... (your mpg123/aplay logic, ensure it waits for completion) ...
                        # mpg123 also blocks by default.
                        linux_played_successfully_this_rep = False
                        try:
                            res_mpg123 = subprocess.run(["mpg123", "-q", temp_file], capture_output=True, text=True) # -q for quiet
                            if res_mpg123.returncode == 0:
                                linux_played_successfully_this_rep = True
                            else:
                                logging.warning(f"mpg123 failed (repetition {i+1}). Code: {res_mpg123.returncode}, Trying aplay.")
                                res_aplay = subprocess.run(["aplay", "-q", temp_file], capture_output=True, text=True) # -q for quiet
                                if res_aplay.returncode == 0:
                                    linux_played_successfully_this_rep = True
                                else:
                                    logging.error(f"aplay also failed (repetition {i+1}). Code: {res_aplay.returncode}")
                        except FileNotFoundError:
                            logging.error("Neither mpg123 nor aplay found for Linux playback.")
                        
                        if linux_played_successfully_this_rep:
                            logging.info(f"Linux playback (repetition {i+1}) successful.")
                            current_repetition_successful = True
                        else:
                            playback_successful_overall = False
                            break

                    if current_repetition_successful and i < repeat_count - 1:
                        logging.debug(f"Waiting a short interval before next repetition.")
                        time.sleep(1) # Optional: short pause between repetitions

                except Exception as play_err:
                    logging.error(f"Error during playback repetition {i + 1}: {play_err}")
                    playback_successful_overall = False
                    break # Stop repeating on error
            
            if not playback_successful_overall:
                 raise RuntimeError("One or more announcement playbacks failed.")
            
            return True # If all repetitions were successful (or not prematurely broken)
            
        except Exception as e:
            logging.error(f"Failed to generate or play audio: {str(e)}")
            # Ensure the exception is re-raised so make_announcement's retry can catch it if needed
            # or its `if not self.speak_announcement()` logic works.
            raise 
            
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    logging.debug("Temporary audio file removed")
                except Exception as e:
                    logging.warning(f"Failed to remove temporary file: {e}")

    @retry_decorator()
    def setup_webdriver(self):
        """Initialize and configure Chrome WebDriver using ChromeDriverManager for robustness."""
        logging.info("Setting up Chrome WebDriver using ChromeDriverManager")
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--ignore-certificate-errors")
            options.add_argument("--allow-insecure-localhost")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--start-maximized")
            # Optional disable gpu and disable software rasterizer
            #options.add_argument("--disable-gpu") 
            #options.add_argument("--disable-software-rasterizer")
            options.add_experimental_option("prefs", {
                "profile.default_content_setting_values.media_stream_mic": 1
            })
            options.add_argument("--headless=new") # Use the newer headless mode
            options.add_argument("--no-sandbox") # Often needed in server/CI environments
            options.add_argument("--disable-dev-shm-usage") # Good practice for Docker/Linux server environments

            # Directly use ChromeDriverManager to install/cache the correct ChromeDriver
            try:
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=options)
                self.active_driver = driver
                logging.info("Chrome WebDriver initialized and assigned to self.active_driver.")
                return driver
            except Exception as e:
                logging.error(f"Failed to initialize Chrome WebDriver via ChromeDriverManager: {str(e)}")
                raise

        except WebDriverException as e:
            error_msg = f"A general WebDriverException occurred during setup: {str(e)}"
            logging.error(error_msg)
            # If self.send_error_notification is available and desired here (outside retry loop)
            if hasattr(self, 'send_error_notification'):
                 self.send_error_notification(
                    subject="PAGING ALERT: CRITICAL - WebDriver Setup Failure",
                    message=(
                        f"Failed to initialize Chrome WebDriver after all attempts):\n"
                        f"Automated announcements cannot be made.\n\n"
                        f"Error: {str(e)}\n\n"
                        f"ACTION REQUIRED: Please investigate the WebDriver/Chrome setup on the server."
                    ),
                    recipients=IT_EMAILS
                )
            raise


    def click_button(self, driver, locator, description, timeout=10, use_js_fallback=True):
        """
        Enhanced button clicking with multiple fallback methods and better error handling
        """
        logging.debug(f"Waiting for element: {description}")
        try:
            element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
            try:
                # Try standard click first
                element.click()
                logging.info(f"Successfully clicked {description}")
                return True
            except ElementClickInterceptedException:
                if use_js_fallback:
                    logging.warning(f"Click intercepted for {description}, trying JavaScript click")
                    driver.execute_script("arguments[0].click();", element)
                    logging.info(f"Successfully clicked {description} using JavaScript")
                    return True
                else:
                    logging.error(f"Click intercepted for {description} and JS fallback disabled")
                    raise
                    
        except TimeoutException:
            logging.warning(f"Timeout waiting for {description} to be clickable")
            if use_js_fallback:
                try:
                    # Try to find element even if not clickable
                    element = driver.find_element(*locator)
                    logging.warning(f"Found element but not clickable, trying JavaScript click for {description}")
                    driver.execute_script("arguments[0].click();", element)
                    logging.info(f"Successfully clicked {description} using JavaScript fallback")
                    return True
                except NoSuchElementException:
                    logging.error(f"Element not found for {description}")
                    return False
            return False
            
        except Exception as e:
            logging.error(f"Error clicking {description}: {str(e)}")
            return False

    @retry_decorator(max_attempts=3, delay=30, escalate_to_email=False)  
    def make_announcement(self, announcement: str, pgroup=HIGHSCHOOL_PGROUP) -> bool:
        """Make the actual announcement through the paging system with enhanced error handling"""
        logging.info(f"Initiating announcement process for group {pgroup}: {announcement}")
        driver = None
        try:
            driver = self.setup_webdriver()
            
            logging.info(f"Navigating to paging system URL: {PAGING_URL}")
            driver.get(PAGING_URL)
            
            # Wait for page to load
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            
            if "login" in driver.current_url.lower():
                logging.info("Login page detected, performing login")
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.ID, "login_username"))
                ).send_keys(LOGIN)
                
                login_btn_result = self.click_button(driver, 
                                  (By.ID, "login-btn"), 
                                  "login button", 
                                  timeout=15)
                if not login_btn_result:
                    raise RuntimeError("Failed to click login button")
                
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.ID, "login_password"))
                ).send_keys(PASSWORD)
                
                login_submit_result = self.click_button(driver, 
                                       (By.ID, "login-btn"), 
                                       "login submit button", 
                                       timeout=15)
                if not login_submit_result:
                    raise RuntimeError("Failed to submit login form")
                
                logging.info("Login credentials entered and submitted")
                
                # Wait for login to complete
                WebDriverWait(driver, 20).until(
                    lambda d: "login" not in d.current_url.lower()
                )
            
            logging.info("Dialing Cyberdata")
            try:
                # Wait for input field and enter extension
                number_input = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Number or Name...']"))
                )
                number_input.clear()
                number_input.send_keys("5999")
            except TimeoutException:
                logging.error("Could not find number input field")
                # Take screenshot for debugging
                screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"number_input_not_found_error_{get_est_now().strftime('%Y%m%d_%H%M%S')}.png")
                driver.save_screenshot(screenshot_path)
                logging.info(f"Saved error screenshot to {screenshot_path}")
                raise RuntimeError("Number input field not found - see error screenshot")
            
            # Multi-level attempt to find and click the paging system
            paging_clicked = False
            
            # Method 1: Try by name
            try:
                logging.debug("Attempting to click paging server by name")
                paging_clicked = self.click_button(
                    driver,
                    (By.XPATH, "//div[@class='name' and contains(text(), 'Paging Server High School')]"),
                    "Paging Server High School button",
                    timeout=10
                )
            except Exception as e:
                logging.warning(f"Method 1 (name) failed: {e}")
            
            # Method 2: Try by extension number
            if not paging_clicked:
                try:
                    logging.debug("Attempting to click paging server by extension")
                    paging_clicked = self.click_button(
                        driver,
                        (By.XPATH, "//span[contains(text(), '5999')]/ancestor::li[@class='ant-list-item list-pointer']"),
                        "Paging by extension number",
                        timeout=10
                    )
                except Exception as e:
                    logging.warning(f"Method 2 (extension) failed: {e}")
            
            # Method 3: Try JavaScript approach
            if not paging_clicked:
                try:
                    logging.debug("Attempting to click paging server using JavaScript")
                    script = """
                    var elements = document.querySelectorAll('div.name');
                    for (var i = 0; i < elements.length; i++) {
                        if (elements[i].innerText.includes('Paging Server')) {
                            elements[i].click();
                            return true;
                        }
                    }
                    
                    // Try by extension number as fallback
                    var spans = document.querySelectorAll('span');
                    for (var i = 0; i < spans.length; i++) {
                        if (spans[i].innerText.includes('5999')) {
                            spans[i].click();
                            return true;
                        }
                    }
                    
                    return false;
                    """
                    paging_clicked = driver.execute_script(script)
                    if paging_clicked:
                        logging.info("Successfully clicked paging server using JavaScript")
                    else:
                        logging.error("Could not find paging server element even with JavaScript")
                except Exception as e:
                    logging.error(f"Method 3 (JavaScript) failed: {e}")
            
            if not paging_clicked:
                # Take screenshot for debugging
                screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"paging_button_error_{get_est_now().strftime('%Y%m%d_%H%M%S')}.png")
                driver.save_screenshot(screenshot_path)
                logging.error(f"All methods to click paging button failed - screenshot saved to {screenshot_path}")
                raise RuntimeError("Failed to click paging button after all attempts")
            
            # Wait for RTC interface to appear
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'rtc-wra')]"))
                )
                logging.info("RTC interface detected")
                time.sleep(2)  # Give extra time for UI to stabilize
            except TimeoutException:
                # Take screenshot for debugging
                screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"rtc_timeout_error_{get_est_now().strftime('%Y%m%d_%H%M%S')}.png")
                driver.save_screenshot(screenshot_path)
                logging.error(f"RTC interface not found - screenshot saved to {screenshot_path}")
                raise RuntimeError("RTC interface did not appear - see error screenshot")
            
            # Click dialpad button
            dialpad_clicked = self.click_button(
                driver,
                (By.XPATH, "//div[contains(@class, 'rtc-opera-btn')]//span[text()='Dialpad']/ancestor::div[contains(@class, 'rtc-opera-btn')]"),
                "dialpad button",
                timeout=15
            )
            
            if not dialpad_clicked:
                # Try alternative XPath for dialpad
                alternative_dialpad_clicked = self.click_button(
                    driver,
                    (By.XPATH, "//div[contains(@class, 'rtc-opera-btn')]//span[contains(text(), 'Dialpad')]"),
                    "alternative dialpad button",
                    timeout=10
                )
                
                if not alternative_dialpad_clicked:
                    # Try JavaScript approach
                    logging.debug("Attempting to click dialpad using JavaScript")
                    js_dialpad_script = """
                    var spans = document.querySelectorAll('span');
                    for (var i = 0; i < spans.length; i++) {
                        if (spans[i].innerText.includes('Dialpad')) {
                            spans[i].click();
                            return true;
                        }
                    }
                    return false;
                    """
                    js_dialpad_clicked = driver.execute_script(js_dialpad_script)
                    if not js_dialpad_clicked:
                        # Take screenshot for debugging
                        screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"dialpad_not_found_error_{get_est_now().strftime('%Y%m%d_%H%M%S')}.png")
                        driver.save_screenshot(screenshot_path)
                        logging.error(f"Failed to find dialpad button - screenshot saved to {screenshot_path}")
                        raise RuntimeError("Could not click dialpad button after all attempts")

            logging.info(f"STARTING DIAL PROCESS for paging group: {pgroup} (Type: {type(pgroup)})") # Verify value and type

            js_digit_clicked = False # Initialize outside the loop for screenshot naming
            digit_clicked = False    # Initialize outside the loop for screenshot naming

            for digit in str(pgroup).strip():
                logging.info(f"Attempting to dial digit: '{digit}' from pgroup '{pgroup}'") # Log each digit attempt
                # Using the locator from your code:
                digit_locator = (By.XPATH, f"//div[contains(@class, 'item-num') and text()='{digit}']")
                logging.debug(f"Using locator: {digit_locator}")

                # --- Attempt standard click using your click_button function ---
                # Assuming self.click_button takes driver, locator, description, timeout
                # If click_button is not a method of self, adjust the call accordingly
                digit_clicked = self.click_button(
                    driver,
                    digit_locator,
                    f"digit {digit}",
                    timeout=10
                )
                js_digit_clicked = False # Reset JS flag for this digit

                # --- Attempt JS click if standard click failed ---
                if not digit_clicked:
                    logging.warning(f"Standard click failed for digit {digit}. Attempting JavaScript click.")
                    js_driver = driver # Ensure driver instance is correct
                    # Adding console logs within the JS for easier debugging in browser dev tools
                    js_digit_script = f"""
                    var digits = document.querySelectorAll('div.item-num');
                    console.log(`JS: Looking for digit: {digit} among {digits.length} elements`);
                    for (var i = 0; i < digits.length; i++) {{
                        // Trim whitespace from innerText for comparison robustness
                        if (digits[i].innerText.trim() === '{digit}') {{
                            console.log(`JS: Found digit {digit} at index {i}, attempting click.`);
                            digits[i].click();
                            return true;
                        }}
                    }}
                    console.log(`JS: Digit {digit} not found.`);
                    return false;
                    """
                    try:
                        js_digit_clicked = js_driver.execute_script(js_digit_script)
                        if not js_digit_clicked:
                            logging.error(f"JavaScript click ALSO failed for digit {digit}")
                        else:
                             logging.info(f"JavaScript click succeeded for digit {digit}")
                    except Exception as js_err:
                        logging.error(f"Error executing JavaScript click for digit {digit}: {js_err}")
                        js_digit_clicked = False # Ensure flag is false on error

                # Determine if the click was ultimately successful (standard or JS)
                click_successful = digit_clicked or js_digit_clicked

                # Optional: Raise error if click failed completely, or decide if you want to continue anyway
                if not click_successful:
                    logging.error(f"BOTH standard and JS clicks failed for digit {digit}. Aborting dial.")
                    raise RuntimeError(f"Failed to click essential digit {digit} for paging group {pgroup}")

                time.sleep(0.5) # Existing sleep

            logging.info(f"COMPLETED DIAL ATTEMPTS for paging group: {pgroup}")
            # --- End of replaced block ---
            
            # Wait a moment for connection to establish
            time.sleep(1)
            
            # Generate and play announcement
            if not self.speak_announcement(announcement):
                raise RuntimeError("Failed to play announcement audio")
            
            # Wait for announcement to finish
            time.sleep(10)
            logging.info("Announcement completed successfully")
            return True
            
        except Exception as e:
            # Capture more details about the error
            error_details = traceback.format_exc()
            logging.error(f"Failed to make announcement: {str(e)}\n{error_details}")
            
            # Take screenshot for debugging if driver is available
            if driver:
                try:
                    screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                                              f"announcement_error_{get_est_now().strftime('%Y%m%d_%H%M%S')}.png")
                    driver.save_screenshot(screenshot_path)
                    logging.info(f"Saved error screenshot to {screenshot_path}")
                except Exception as screenshot_error:
                    logging.warning(f"Could not save error screenshot: {str(screenshot_error)}")
                    
            # Let notification system handle the error notification
            raise
            
        finally:
            if driver:
                try:
                    # Try to hang up call
                    self.click_button(driver,
                                  (By.XPATH, "//div[contains(@class, 'opera-icon-wra icon-end')]"),
                                  "hang-up button",
                                  timeout=5)
                    logging.info("Call ended successfully")
                except Exception as e:
                    logging.warning(f"Failed to click hang-up button: {e}")
                finally:
                    try:
                        driver.quit()
                        if self.active_driver is driver:
                            self.active_driver = None
                        logging.debug("WebDriver closed")
                    except Exception as e:
                        logging.warning(f"Error closing WebDriver: {e}")

    def perform_health_check(self) -> bool:
        """
        Perform a system health check to verify all components are functioning
        Returns True if all systems are operational, False otherwise
        """
        now = get_est_now()
        
        # Run health check no more than once per hour
        if self.health_check_last_run and (now - self.health_check_last_run) < timedelta(hours=1):
            logging.debug("Skipping health check - last check was less than 1 hour ago")
            return True
            
        logging.info("Running system health check")
        health_status = True
        issues = []
        
        # Check 1: Internet connectivity
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            logging.info("OK Internet connection available")
        except (socket.error, socket.timeout) as e:
            health_status = False
            error_msg = f"Internet connection check failed: {str(e)}"
            logging.error(error_msg)
            issues.append(error_msg)
            
        # Check 2: Google Calendar API connection
        if self.calendar_service:
            try:
                # Just a basic call to verify API is working
                self.calendar_service.calendarList().list(maxResults=1).execute()
                logging.info("OK Google Calendar API connection working")
            except Exception as e:
                health_status = False
                error_msg = f"Google Calendar API check failed: {str(e)}"
                logging.error(error_msg)
                issues.append(error_msg)
                # Try to re-authenticate if calendar fails
                try:
                    logging.info("Attempting to re-authenticate with Google Calendar")
                    self.authenticate_calendar()
                except Exception as auth_error:
                    logging.error(f"Re-authentication failed: {str(auth_error)}")
        else:
            health_status = False
            error_msg = "Google Calendar service not initialized"
            logging.error(error_msg)
            issues.append(error_msg)
            
        # Check 3: ElevenLabs API connection
        try:
            # Simple API call to verify connection
            headers = {"xi-api-key": ELEVENLABS_API_KEY}
            response = requests.get("https://api.elevenlabs.io/v1/voices", headers=headers)
            if response.status_code == 200:
                logging.info("OK ElevenLabs API connection working")
            else:
                health_status = False
                error_msg = f"ElevenLabs API check failed: Status code {response.status_code}"
                logging.error(error_msg)
                issues.append(error_msg)
        except Exception as e:
            health_status = False
            error_msg = f"ElevenLabs API check failed: {str(e)}"
            logging.error(error_msg)
            issues.append(error_msg)
            
        # Check 4: Paging system connectivity (simple network ping)
        try:
            # Extract hostname from URL
            host = PAGING_URL.split("://")[1].split(":")[0]
            socket.gethostbyname(host)  # Just try to resolve hostname
            logging.info(f"OK Paging system host {host} is reachable")
        except Exception as e:
            health_status = False
            error_msg = f"Paging system host check failed: {str(e)}"
            logging.error(error_msg)
            issues.append(error_msg)
            
        # Check 5: Email notification system
        if issues:  # Only test email if there are other issues
            try:
                test_result = self.send_error_notification(
                    subject="Paging System - Health Check Failure Detected", # Clarified subject
                    message=(
                        "The automated system health check has detected one or more issues requiring IT attention:\n\n" + 
                        "\n".join(f"- {issue}" for issue in issues) +
                        "\n\nPlease review the system logs for more details."
                    ),
                    recipients=IT_EMAILS # Explicitly to IT
                )
                if test_result:
                    logging.info("OK Email notification system working")
                else:
                    health_status = False
                    error_msg = "Email notification system check failed"
                    logging.error(error_msg)
                    issues.append(error_msg)
            except Exception as e:
                health_status = False
                error_msg = f"Email notification system check failed: {str(e)}"
                logging.error(error_msg)
                issues.append(error_msg)
                
        self.health_check_last_run = now
        
        logging.info(f"Health check completed - Status: {'PASS' if health_status else 'FAIL'}")
        return health_status

    @internet_connection_decorator
    def check_calendar(self):
        logging.debug("Starting calendar check")
        cache_file = os.path.join(SCRIPT_DIR, 'event_cache.json')
        today_iso = get_est_now().date().isoformat()
        cached_events_data = {}

        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    loaded_cache = json.load(f)
                    if loaded_cache.get('date') == today_iso:
                        cached_events_data = loaded_cache.get('events', {})
                        logging.info(f"Loaded {len(cached_events_data)} events from cache for today.")
            except Exception as e:
                logging.warning(f"Failed to load event cache: {e}")

        try:
            est_tz = ZoneInfo("America/New_York")
            now = get_est_now()

            if self.last_announcement_time:
                cooldown_period = timedelta(minutes=5)
                if (now - self.last_announcement_time) < cooldown_period:
                    logging.debug(f"In cooldown period. {(cooldown_period - (now - self.last_announcement_time)).seconds // 60} min remaining.")
                    return

            logging.info(f"Current EST time: {now.strftime('%I:%M:%S %p %Z')}")

            today_midnight_est = now.replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_midnight_est = today_midnight_est + timedelta(days=1)
            time_min_iso = today_midnight_est.isoformat()
            time_max_iso = tomorrow_midnight_est.isoformat()

            if not self.calendar_service:
                logging.warning("Calendar service not initialized. Attempting authentication.")
                self.authenticate_calendar()
                if not self.calendar_service:
                    logging.error("Failed to initialize calendar service after authentication attempt.")
                    return

            calendar_list_result = self.calendar_service.calendarList().list().execute()
            available_calendars_map = {item['summary']: item['id'] for item in calendar_list_result.get('items', [])}
            logging.debug(f"Available calendars: {', '.join(available_calendars_map.keys())}")

            all_eligible_events_today = [] 
            
            for calendar_name_from_config in CALENDAR_IDS:
                if calendar_name_from_config not in available_calendars_map:
                    logging.warning(f"Calendar '{calendar_name_from_config}' not found in user's available calendars.")
                    continue

                calendar_id = available_calendars_map[calendar_name_from_config]
                try:
                    events_result = self.calendar_service.events().list(
                        calendarId=calendar_id,
                        timeMin=time_min_iso,
                        timeMax=time_max_iso,
                        singleEvents=True,
                        orderBy='startTime',
                        timeZone='America/New_York' 
                    ).execute()
                    
                    fetched_events = events_result.get('items', [])
                    if fetched_events:
                        logging.debug(f"Fetched {len(fetched_events)} events for calendar '{calendar_name_from_config}'")

                    for event_data in fetched_events:
                        event_id = event_data['id']
                        if event_id in cached_events_data:
                            event_data.update(cached_events_data[event_id])
                        
                        event_data['source_calendar_name'] = calendar_name_from_config # Store original calendar name

                        title = event_data.get('summary', '')
                        description = event_data.get('description', '')

                        if any(word in title.lower() for word in ['cancel', 'cancelled', 'canceled', 'postponed']):
                            continue

                        time_match = re.search(r"Dismiss\s+(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|noon|midnight)", 
                                             description, re.IGNORECASE)
                        if time_match:
                            dismiss_time_str = clean_time_string(time_match.group(1))
                            if dismiss_time_str:
                                try:
                                    dismiss_time_obj = datetime.strptime(dismiss_time_str, "%I:%M %p").time()
                                    dismiss_datetime_est = datetime.combine(now.date(), dismiss_time_obj, tzinfo=est_tz)
                                    
                                    time_difference_seconds = (dismiss_datetime_est - now).total_seconds()

                                    if 0 <= time_difference_seconds <= 60: # Announcement window: 0 to 60 seconds past dismiss time
                                        logging.info(f"Event eligible: '{title}' from '{calendar_name_from_config}' (Dismiss at {dismiss_time_str}).")
                                        all_eligible_events_today.append(event_data)
                                        # Update cache with this processed event
                                        cached_events_data[event_id] = {'processed_for_announcement': True, 'dismiss_time_str': dismiss_time_str}

                                    elif dismiss_datetime_est < now and not cached_events_data.get(event_id, {}).get('processed_for_announcement'):
                                        logging.debug(f"Missed announcement window for '{title}' (Dismissed at {dismiss_time_str}). Marking as processed.")
                                        cached_events_data[event_id] = {'processed_for_announcement': True, 'dismiss_time_str': dismiss_time_str}

                                except ValueError as ve:
                                    logging.error(f"Error parsing dismiss time '{dismiss_time_str}' for event '{title}': {ve}")
                except HttpError as http_err:
                    logging.error(f"HttpError fetching events for calendar '{calendar_name_from_config}': {http_err}")
                except Exception as e:
                    logging.error(f"Unexpected error fetching events for calendar '{calendar_name_from_config}': {e}")
            
            try:
                with open(cache_file, 'w') as f:
                    json.dump({'date': today_iso, 'events': cached_events_data}, f)
                logging.info(f"Event cache updated for date {today_iso}.")
            except Exception as e:
                logging.warning(f"Failed to save event cache: {e}")

            if not all_eligible_events_today:
                logging.debug("No events currently eligible for announcement.")
                return

            # Group eligible events by exact dismiss time string for combined announcements
            announcement_groups = {} # Key: dismiss_time_str, Value: List of event_data dicts
            for event_data in all_eligible_events_today:
                description = event_data.get('description', '')
                time_match = re.search(r"Dismiss\s+(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|noon|midnight)", 
                                     description, re.IGNORECASE)
                if time_match: # Should always match if it's in all_eligible_events_today
                    dismiss_time_str = clean_time_string(time_match.group(1))
                    if dismiss_time_str:
                        if dismiss_time_str not in announcement_groups:
                            announcement_groups[dismiss_time_str] = []
                        announcement_groups[dismiss_time_str].append(event_data)
            
            logging.info(f"Found {len(announcement_groups)} distinct dismissal times to process for announcements.")

            for dismiss_time_key, events_in_group in announcement_groups.items():
                processed_team_names = [extract_team_names(event.get('summary', 'Unknown Team')) for event in events_in_group]
                original_calendar_names_for_group = list(set(event.get('source_calendar_name', 'Unknown Calendar') for event in events_in_group))

                # Filter out duplicates from processed_team_names if any
                unique_processed_team_names = []
                for team in processed_team_names:
                    if team not in unique_processed_team_names:
                        unique_processed_team_names.append(team)
                
                if not unique_processed_team_names:
                    logging.warning(f"No team names extracted for dismissal at {dismiss_time_key}. Skipping.")
                    continue

                announcement_text = format_team_announcement(unique_processed_team_names)
                pgroup_to_use = self.determine_pgroup_for_teams(unique_processed_team_names, original_calendar_names_for_group)

                logging.info(f"Preparing announcement for {dismiss_time_key} (Teams: {', '.join(unique_processed_team_names)}) using pgroup {pgroup_to_use}: {announcement_text}")

                try:
                    # The make_announcement method will handle its own retries
                    # escalate_to_email is False for make_announcement's decorator
                    self.make_announcement(announcement_text, pgroup=pgroup_to_use)
                    
                    self.last_announcement_time = get_est_now() # Use current time after successful announcement
                    logging.info(f"Announcement for {dismiss_time_key} (PGroup: {pgroup_to_use}) completed successfully.")
                    # Mark these events as processed in cache after successful announcement
                    for event_data in events_in_group:
                        cached_events_data[event_data['id']] = {'processed_for_announcement': True, 'dismiss_time_str': dismiss_time_key}
                    try:
                        with open(cache_file, 'w') as f:
                            json.dump({'date': today_iso, 'events': cached_events_data}, f)
                    except Exception as e:
                        logging.warning(f"Failed to update cache after successful announcement: {e}")

                except Exception as e: # Catches final failure of make_announcement
                    logging.error(f"FINAL FAILURE to make announcement for '{dismiss_time_key}' - Teams: {', '.join(unique_processed_team_names)} - PGroup: {pgroup_to_use}. Error: {str(e)}")
                    
                    email_subject = f"ACTION REQUIRED: Paging Announcement Failure at {dismiss_time_key}"
                    email_body = (
                        f"The automated paging system failed to make an announcement.\n"
                        f"Please make the following announcement manually as soon as possible.\n\n"
                        f"Dismissal Time: {dismiss_time_key}\n"
                        f"Team(s): {', '.join(unique_processed_team_names)}\n"
                        f"Announcement Text: \"{announcement_text}\"\n"
                        f"Paging Group Used/Attempted: {pgroup_to_use}\n\n"
                        f"--------------------------------------------------\n"
                        f"Error Details (for IT troubleshooting):\n"
                        f"Error: {str(e)}\n"
                        f"Full error traceback may be in the system logs (integrated_paging.log).\n"
                        f"--------------------------------------------------"
                    )
                    self.send_error_notification(
                        subject=email_subject,
                        message=email_body,
                        recipients=ANNOUNCEMENT_FAILURE_EMAILS # Secretary + IT
                    )
                    # Mark as processed even on failure to prevent immediate re-try of a known failing announcement
                    for event_data in events_in_group:
                         cached_events_data[event_data['id']] = {'processed_for_announcement': True, 'dismiss_time_str': dismiss_time_key, 'error_on_processing': True}
                    try:
                        with open(cache_file, 'w') as f:
                            json.dump({'date': today_iso, 'events': cached_events_data}, f)
                    except Exception as e_cache:
                        logging.warning(f"Failed to update cache after failed announcement: {e_cache}")


        except Exception as e: # Broad exception handler for the whole check_calendar process
            error_details = traceback.format_exc()
            logging.error(f"Major error during calendar check process: {str(e)}\n{error_details}")
            self.send_error_notification(
                subject="Paging System - Calendar Check Process Failure",
                message=(
                    f"The calendar check process encountered an unexpected major error and requires IT attention.\n"
                    f"This may prevent new announcements from being scheduled.\n\n"
                    f"Error: {str(e)}\n\n"
                    f"Stack Trace (see logs for more detail if truncated):\n{error_details[:2000]}"
                ),
                recipients=IT_EMAILS
            )
            # Do not re-raise here if you want the main loop to continue robustly
            # Or re-raise if this should halt and be caught by the main loop's retry logic
        finally:
            logging.debug("Calendar check finished. Performing garbage collection.")
            gc.collect()

    def run(self, check_interval=60):
        """
        Enhanced main loop with error recovery, system health checks, and status management
        """
        # Add status file management
        status_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'service_status.json')
        
        def update_status(status, message=None):
            try:
                with open(status_file, 'w') as f:
                    json.dump({
                        'status': status,
                        'last_update': get_est_now().isoformat(),
                        'message': message
                    }, f)
            except Exception as e:
                logging.warning(f"Failed to update status file: {e}")
        
        update_status('starting')
        
        logging.info("Starting integrated calendar and paging system.")
        logging.info(f"Highschool Paging Group: {HIGHSCHOOL_PGROUP}.    Highschool and Sixth Paging Group: {HIGHSCHOOL_AND_SIXTH_PGROUP}.  Highschool, Sixth, and Elementary Paging Group: {HIGHSCHOOL_SIXTH_AND_FIFTH_PGROUP}")
        
        consecutive_failures = 0
        max_consecutive_failures = 5
        health_check_counter = 0
        health_check_interval = 60
        cleanup_counter = 0
        cleanup_interval = 10
        
        try:
            self.authenticate_calendar()
            self.perform_health_check()
            
            while not self.shutdown_requested:
                try:
                    now_est = get_est_now()
                    scheduled_shutdown_dt = now_est.replace(hour=SHUTDOWN_HOUR, minute=SHUTDOWN_MINUTE, second=0, microsecond=0)
                    
                    if now_est >= scheduled_shutdown_dt:
                        logging.info(f"Current time {now_est} is past scheduled shutdown time {scheduled_shutdown_dt.strftime('%H:%M:%S')}. Initializing shutdown.")
                        self.shutdown_requested = True
                        update_status('stopping', "Scheduled shutdown time reached.")
                        break
                    
                    if self.shutdown_requested:
                        break
                    
                    health_check_counter += 1
                    cleanup_counter += 1
                    
                    # Run health check periodically
                    if health_check_counter >= health_check_interval:
                        self.perform_health_check()
                        health_check_counter = 0
                    
                    # Perform memory cleanup periodically
                    if cleanup_counter >= cleanup_interval:
                        logging.debug("Performing memory cleanup")
                        self.last_processed_events.clear()
                        
                        gc.collect()
                        cleanup_counter = 0
                    
                    logging.info("Starting calendar check cycle")
                    update_status('running', f"Last check: {get_est_now().isoformat()}")
                    self.check_calendar()
                    consecutive_failures = 0
                    
                    logging.info(f"Calendar check complete, waiting {check_interval} seconds...")
                    for _ in range(check_interval):
                        if self.shutdown_requested:
                            break
                        time.sleep(1)
                    if self.shutdown_requested:
                        break
                    
                except Exception as e_loop:
                    update_status('error', str(e_loop))
                    consecutive_failures += 1
                    logging.error(f"Error in main loop (failure {consecutive_failures}/{max_consecutive_failures}): {str(e_loop)}", exc_info=True)
                    
                    # If we've hit the threshold for consecutive failures, notify and retry authentication
                    if consecutive_failures >= max_consecutive_failures:
                        logging.critical(f"Reached {max_consecutive_failures} consecutive failures, attempting recovery")
                        self.send_error_notification(
                            subject=f"PAGING ALERT: System Experiencing Repeated Failures ({consecutive_failures} consecutive)", # Clarified subject
                            message=(
                                f"The paging system has experienced {consecutive_failures} consecutive failures in its main loop.\n\n"
                                f"Last error encountered: {str(e)}\n\n"
                                f"The system will attempt to recover by re-authenticating and performing a health check.\n"
                                f"IT intervention may be required if issues persist."
                            ),
                            recipients=IT_EMAILS # Explicitly to IT
                        )
                        
                        # Try to recover by re-authenticating
                        try:
                            self.authenticate_calendar()
                            self.perform_health_check()
                            consecutive_failures = 0  # Reset counter if recovery succeeds
                        except Exception as recovery_error:
                            logging.critical(f"Recovery attempt failed: {str(recovery_error)}")
                            
                    # Wait before retrying, with increasing backoff
                    retry_delay = min(check_interval * (2 ** (consecutive_failures - 1)), 900)  # Max 15 minutes
                    logging.info(f"Waiting {retry_delay} seconds before retrying...")
                    for _ in range(retry_delay):
                        if self.shutdown_requested:
                            break
                        time.sleep(1)
                    if self.shutdown_requested:
                        break
                    
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received, shutting down gracefully, like a lil butterfly")
            self.shutdown_requested = True
            update_status('stopping', "Keyboard interrupt received")
        except SystemExit:
            logging.info("SystemExit caught, initiating graceul shutdown, like a lil butterfly.")
            self.shutdown_requested = True
            update_status('stopping', "SystemExit called")
        except Exception as e:
            error_details = traceback.format_exc()
            logging.critical(f"Fatal error in main process: {str(e)}\n{error_details}")
            update_status('error', f"Fatal run setup error: {str(e)}")
            self.send_error_notification(
                subject="PAGING ALERT: SYSTEM CRITICAL FAILURE - SHUTTING DOWN", # Emphasized subject
                message=(
                    f"The paging system has encountered a fatal unrecoverable error in its main process and is shutting down.\n"
                    f"Manual intervention by IT is required.\n\n"
                    f"Error: {str(e)}\n\n"
                    f"Details:\n{error_details}"
                ),
                recipients=IT_EMAILS # Explicitly to IT
            )
        finally:
            logging.info("Paging system shutting down")
            if hasattr(self, 'active_driver') and self.active_driver:
                logging.info("Closing any active WebDriver instance during final shutdown")
                try:
                    self.active_driver.quit()
                    self.active_driver = None
                except Exception as wd_error:
                    logging.warning(f"Error quitting WebDriver during final shutdown: {wd_error}")
                update_status('stopped', "Paging system shutdown process completed.")
                logging.info("Paging system run() method finally block completed.")

app = Flask(__name__)

# Inside CalendarPagingIntegrationFinalVersion.py

# Ensure _system_instance_for_signal is declared global if it's not already clear
# It is declared global in your main() which is good.

@app.route('/announce', methods=['POST'])
def announce_route():
    # Use the global instance that main() sets up
    global _system_instance_for_signal

    # Get current time for logging context
    request_time_est = get_est_now().strftime('%Y-%m-%d %H:%M:%S %Z')
    client_ip = request.remote_addr

    logging.info(f"FLASK ({request_time_est} from {client_ip}): Received POST request to /announce")

    try:
        data = request.get_json()
        if not data:
            logging.warning(f"FLASK ({request_time_est} from {client_ip}): Request body is empty or not JSON.")
            return jsonify({'success': False, 'error': 'Request body is empty or not valid JSON.'}), 400
        
        logging.debug(f"FLASK ({request_time_est} from {client_ip}): Received JSON data: {data}")

        if 'announcement' not in data:
            logging.warning(f"FLASK ({request_time_est} from {client_ip}): 'announcement' key missing from JSON payload.")
            return jsonify({'success': False, 'error': "Missing 'announcement' field in JSON payload."}), 400

        announcement_text = data['announcement']
        # Optionally get pgroup from request, otherwise use default
        pgroup = data.get('pgroup', HIGHSCHOOL_PGROUP) # Assuming HIGHSCHOOL_PGROUP is a defined constant

        if not isinstance(announcement_text, str) or not announcement_text.strip():
            logging.warning(f"FLASK ({request_time_est} from {client_ip}): 'announcement' text is empty or not a string.")
            return jsonify({'success': False, 'error': "'announcement' must be a non-empty string."}), 400


        if _system_instance_for_signal:
            logging.info(f"FLASK ({request_time_est} from {client_ip}): Attempting to make announcement: '{announcement_text}' to pgroup '{pgroup}'")
            
            # The make_announcement method is decorated with retry_decorator,
            # so it will handle its own retries and raise an exception if all attempts fail.
            _system_instance_for_signal.make_announcement(announcement_text, pgroup=pgroup)
            
            logging.info(f"FLASK ({request_time_est} from {client_ip}): Announcement initiated successfully via API for text: '{announcement_text}'")
            return jsonify({'success': True, 'message': 'Announcement initiated successfully.'})
        else:
            logging.error(f"FLASK ({request_time_est} from {client_ip}): Paging system instance not available for API request.")
            return jsonify({'success': False, 'error': 'Paging system not initialized or not ready.'}), 503 # Service Unavailable

    except Exception as e:
        # This will catch errors from request.get_json(), or if _system_instance_for_signal.make_announcement()
        # raises an exception that isn't caught internally by make_announcement itself (which it should be due to retry_decorator).
        # However, if make_announcement *does* re-raise after retries, this will catch it.
        error_details = traceback.format_exc()
        logging.error(f"FLASK ({request_time_est} from {client_ip}): Unhandled exception in /announce route: {str(e)}\n{error_details}")
        return jsonify({'success': False, 'error': f"An internal server error occurred: {str(e)}"}), 500

def run_flask_app():
    app.run(host="0.0.0.0", port=8467, debug=True, use_reloader=False, threaded=True)

def main_signal_handler(sig, frame):
    global _system_instance_for_signal
    
    signal_name = signal.Signals(sig).name
    logging.warning(f"MAIN SIGNAL HANDLER: Received signal {signal_name} ({sig}). Requesting shutdown")
    if _system_instance_for_signal:
        _system_instance_for_signal.shutdown_requested = True
    else:
        logging.warning("MAIN SIGNAL HANDLER: System instance not found for graeful shutdown request")
        sys.exit(1)
        
def main():
    global _system_instance_for_signal

    system_instance = None
    try:
        system_instance = IntegratedPagingSystem()
        _system_instance_for_signal = system_instance
        
        # Start the Flask server in a separate thread
        flask_thread = threading.Thread(target=run_flask_app, name="FlaskThread")
        flask_thread.daemon = True
        flask_thread.start()
        logging.info("Flask application thread started.")
        
        signal.signal(signal.SIGINT, main_signal_handler)
        logging.info("Starting IntegratedPagingSystem run loop in the main thread")
        system_instance.run()
        
    except SystemExit:
        logging.info("SystemExit caught in main. Application will terminate.")
    except Exception as e:
        logging.critical(f"Fatal error in main execution: {str(e)}", exc_info=True)
        if system_instance and hasattr(system_instance, 'send_error_notification'):
            system_instance.send_error_notification(
                subject="PAGING ALERT: SYSTEM CRITICAL FAILURE - UNHANDLED IN MAIN",
                message=(
                    f"The paging system encountered a critical error in main() and is shutting down.\n"
                    f"Error: {str(e)}\n\nDetails\n{traceback.format_exc()}"
                ),
                recipient=IT_EMAILS
            )
        else:
            print(f"Fatal error: {str(e)}\n{traceback.format_exc()}", file=sys.stderr)
            try:
                msg = MIMEMultipart()
                msg['From'] = EMAIL_USER
                msg['To'] = ", ".join(IT_EMAILS)
                msg['Subject'] = "CRITICAL: Paging System Crash"
                
                body = f"Time: {get_est_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                body += f"Host: {socket.gethostname()}\n\n"
                body += f"The paging system has crashed and could not recover:\n\n{str(e)}\n\n"
                body += f"Stack trace:\n{traceback.format_exc()}"
                msg.attach(MIMEText(body, 'plain'))
                
                with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                    server.starttls()
                    server.login(EMAIL_USER, EMAIL_PASSWORD)
                    server.send_message(msg)
            except Exception as final_email_err:
                print(f"Failed to send final crash email: {final_emial_err}", file=sys.stderr)
        
    finally:
        logging.info("Application terminated")

if __name__ == "__main__":
    main()

# curl -X POST http://localhost:8467/announce \
#    -H "Content-Type: application/json" \
#    -d '{"announcement": "Fire drill at 2 PM today."}'