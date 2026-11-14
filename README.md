# Calendar Paging Announcer

This Python application integrates Google Calendar with a web-based paging system to automatically announce team dismissals for a school. It monitors multiple Google Calendar sports schedules, identifies eligible dismissal events, generates voice announcements using ElevenLabs Text-to-Speech API, and delivers these announcements via a Selenium-controlled interface to the paging system. The system is designed for continuous operation with a scheduled daily shutdown and includes an optional Flask API for on-demand announcements.

## Core Features

- **Automated Calendar Monitoring:** Continuously checks specified Google Calendars for dismissal events.
- **Dynamic Event Processing:** Extracts team names and dismissal times from calendar event descriptions.
- **Text-to-Speech (TTS):** Utilizes ElevenLabs API to generate natural-sounding voice announcements.
- **Web Paging System Integration:** Uses Selenium and ChromeDriver to navigate and operate a web-based paging interface.
- **Configurable Paging Groups:** Determines appropriate paging groups based on team characteristics (e.g., presence of 5th graders, sport type).
- **Scheduled Operation:** Runs continuously during configurable hours with an automated graceful shutdown.
- **Graceful Shutdown:** Supports Ctrl+C for manual shutdown and includes an automated timed daily shutdown.
- **Robust Error Handling:** Includes retry mechanisms for network-dependent operations (calendar, TTS, paging system interaction) and comprehensive error logging.
- **Email Notifications:** Sends email alerts for critical errors, announcement failures, and health check issues to designated recipients.
- **Health Checks:** Periodically checks connectivity to all essential services (Internet, Google Calendar, ElevenLabs, Paging System).
- **Event Caching:** Caches processed events for the current day to avoid duplicate announcements.
- **Status Logging:** Maintains a `service_status.json` file indicating the current operational status.
- **Flask API (Optional):** Includes a simple Flask server with an `/announce` endpoint for triggering on-demand announcements.

## Getting Started

### Prerequisites

Ensure you have the following installed and configured:
- Python 3.9+
- Google Chrome browser
- Access to the target web-based paging system
- Google Calendar API credentials (`credentials.json`)
- An ElevenLabs API key

For a detailed list of Python packages, see the `requirements.txt` file.

### Installation

1.  **Clone the Repository:**
    ```bash
    git clone [your-repository-url]
    cd [your-repository-directory]
    ```

2.  **Set Up a Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    # On Windows
    venv\Scripts\activate
    # On macOS/Linux
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    Install the required Python packages using the provided `requirements.txt` file:
    ```bash
    pip install -r requirements.txt
    ```
    The `webdriver-manager` package will automatically download and manage the correct ChromeDriver version.

4.  **Google Calendar API Setup:**
    * Follow the instructions at [Google Cloud Console Python Quickstart](https://developers.google.com/calendar/api/quickstart/python) to enable the Google Calendar API for your project.
    * Download your OAuth 2.0 client secrets file and save it as `credentials.json` in the root directory of this project (alongside `CalendarPagingIntegration.py`).
    * The first time you run the script, it will guide you through an authentication flow in your browser to generate a `token.pickle` file. This file stores your access tokens for future use.

### Configuration

The script is configured through constants at the top of `CalendarPagingIntegration.py` and environment variables.

1.  **Environment Variables (Recommended for sensitive data):**
    Create a `.env` file in the project root (ensure this file is listed in your `.gitignore`) or set these variables in your operating system environment:
    * `PAGING_LOGIN`: Username for the web paging system.
    * `PAGING_PASSWORD`: Password for the web paging system.
    * `ELEVENLABS_API_KEY`: Your API key for ElevenLabs.
    * `EMAIL_USER`: Email account used to send notifications.
    * `EMAIL_PASSWORD`: Password for that account (if using Gmail, this would be an App Password).
    * `ELEVENLABS_VOICE_ID`: The ElevenLabs Voice ID to be used for announcements.
    * `PAGING_URL`: The URL of your web-based paging system.
    * `SECRETARY_EMAILS`, `IT_EMAILS`: Comma-separated lists of notification recipients.

    No credentials are stored in the code; all secrets must come from the environment.

2.  **Script Constants (in `CalendarPagingIntegration.py`):**
    Review and update these constants directly in the script as needed:
    * `SHUTDOWN_HOUR`, `SHUTDOWN_MINUTE`: The hour and minute (24-hour format, EST) for the script's scheduled daily shutdown.
    * `SMTP_SERVER`, `SMTP_PORT`: Configuration for the email notification system.
    * `HIGHSCHOOL_PGROUP`, `HIGHSCHOOL_AND_SIXTH_PGROUP`, `HIGHSCHOOL_SIXTH_AND_FIFTH_PGROUP` (and potentially `PGROUP_WITH_5TH_GRADERS` if you define it): Paging group IDs.
    * `*_HAS_FIFTH_GRADERS` booleans (e.g., `BASKETBALL_MS_BOYS_HAS_FIFTH_GRADERS`): Set these to `True` or `False` based on team compositions.
    * `CALENDAR_IDS`: A list of Google Calendar names (as they appear in your Google Calendar list) to monitor.

### Running the Script

1.  **Activate your virtual environment (if used).**
2.  **Set up environment variables (if not already set in your OS).**
3.  **Ensure `credentials.json` is present.**
4.  **Launch the script:**
    * It's recommended to run the script via a batch file (Windows) or shell script (Linux/macOS) that can manage its window or run it as a background process.
    * **Example `RunPaging.bat` (Windows):**
        ```batch
        @echo off
        echo Starting Calendar Paging Process...
        REM The /c flag ensures the cmd window closes when the Python script exits.
        start "Calendar Paging Process" cmd /c python "C:\path\to\your\script\CalendarPagingIntegration.py"
        echo Batch file has launched the Python script.
        ```
        *(Adjust the path to your Python script accordingly.)*

The system will then:
1.  Authenticate with Google Calendar (may require browser interaction on the first run).
2.  Start the Flask API server on port 8467 (if enabled).
3.  Continuously monitor the configured calendars for dismissal events.
4.  Make announcements via the paging system when eligible events are found.
5.  Log all activities to `integrated_paging.log` in the script's directory.
6.  Automatically shut down at the time specified by `SHUTDOWN_HOUR` and `SHUTDOWN_MINUTE`.

## Calendar Event Formatting

For the system to correctly process dismissals, Google Calendar events should include:

* **Event Title:** Typically includes the team name(s) (e.g., "Varsity Boys Soccer @ Opponent"). The script primarily extracts the home team name before an "@" symbol.
* **Event Description:** Must contain the dismissal time in a recognizable format, prefixed by "Dismiss".
    * **Example:** `Dismiss 2:15 PM`
    * The script attempts to parse various time formats (e.g., "Dismiss 2.15pm", "Dismiss 2-15 p.m.").

## Logging and Status

* **`integrated_paging.log`:** Contains detailed operational logs, including calendar checks, announcement attempts, errors, and health check results.
* **`service_status.json`:** A JSON file updated by the script to indicate its current status (e.g., 'starting', 'running', 'stopping', 'stopped', 'error').
* **`event_cache.json`:** Stores information about events processed for the current day to prevent duplicate announcements and handle missed windows.
* **`token.pickle`:** Stores Google Calendar API access and refresh tokens after successful authentication.

## Flask API (Optional)

If the Flask component is active, you can trigger announcements via HTTP POST requests:
* **Endpoint:** `http://<script-host-ip>:8467/announce`
* **Method:** `POST`
* **Content-Type:** `application/json`
* **Body (JSON):**
    ```json
    {
        "announcement": "This is a test announcement.",
        "pgroup": "40" // Optional, defaults to HIGHSCHOOL_PGROUP
    }
    ```

    **Example using `curl`:**
    ```bash
    curl -X POST http://localhost:8467/announce \
       -H "Content-Type: application/json" \
       -d '{"announcement": "The library will be closing in 15 minutes.", "pgroup": "40"}'
    ```

## Troubleshooting

* **Authentication Issues:**
    * Ensure `credentials.json` is valid and in the correct directory.
    * Delete `token.pickle` to force re-authentication if token issues are suspected.
    * Check Google Cloud Console for API quota limits or errors.
* **WebDriver Issues:**
    * `webdriver-manager` should handle ChromeDriver, but ensure Google Chrome is up to date.
    * Firewall or antivirus software might interfere with WebDriver or Chrome.
* **Paging System Access:**
    * Verify the `PAGING_URL`, `LOGIN`, and `PASSWORD` are correct.
    * Ensure the machine running the script can access the `PAGING_URL`.
* **No Announcements:**
    * Check `integrated_paging.log` for errors during calendar fetching or event processing.
    * Verify calendar event descriptions contain "Dismiss" followed by a valid time.
    * Ensure `CALENDAR_IDS` in the script match the exact names of your Google Calendars.
* **Check `service_status.json`** for the last reported status of the script.

## Contributing

1.  Fork the repository.
2.  Create a new branch for your feature or bug fix (`git checkout -b feature/your-feature-name`).
3.  Make your changes and commit them (`git commit -am 'Add some feature'`).
4.  Push to the branch (`git push origin feature/your-feature-name`).
5.  Create a new Pull Request.

---
