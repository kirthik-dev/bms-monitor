import os
import re
import sys
import time
import logging
import argparse
import requests
from datetime import datetime
from urllib.parse import urlparse

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BMSMonitor")

# ──────────────────────────────────────────────────────────────────────
# TELEGRAM CONFIGURATION FALLBACKS
# Hardcode your fallback credentials below if not using env vars:
# ──────────────────────────────────────────────────────────────────────
FALLBACK_BOT_TOKEN = "8858131574:AAGCcS7bRWDBqn3cABFNsHp5LRvWyLxXRmI"
FALLBACK_CHAT_ID = "-5378436688"

# Priority: Environment Variables -> Hardcoded fallbacks
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or FALLBACK_BOT_TOKEN
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or FALLBACK_CHAT_ID

API_URL = "https://in.bookmyshow.com/api/movies-data/v4/showtimes-by-event/primary-dynamic"

REGION_MAP = {
    "chennai":    ("CHEN",   "chennai",    "13.056", "80.206", "tf3"),
    "mumbai":     ("MUMBAI", "mumbai",     "19.076", "72.878", "te7"),
    "delhi-ncr":  ("NCR",    "delhi-ncr",  "28.613", "77.209", "ttn"),
    "delhi":      ("NCR",    "delhi-ncr",  "28.613", "77.209", "ttn"),
    "bengaluru":  ("BANG",   "bengaluru",  "12.972", "77.594", "tdr"),
    "bangalore":  ("BANG",   "bengaluru",  "12.972", "77.594", "tdr"),
    "hyderabad":  ("HYD",    "hyderabad",  "17.385", "78.487", "tep"),
    "kolkata":    ("KOLK",   "kolkata",    "22.573", "88.364", "tun"),
    "pune":       ("PUNE",   "pune",       "18.520", "73.856", "te2"),
    "kochi":      ("KOCH",   "kochi",      "9.932",  "76.267", "t9z"),
    "coimbatore": ("COIM",   "coimbatore", "11.017", "76.956", "t9y"),
}

def parse_bms_url(url: str) -> dict:
    """Extracts event_code, date_code, and region_slug from a BMS URL."""
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    result = {"event_code": None, "date_code": None, "region_slug": None}
    for p in parts:
        if re.match(r"^ET\d{8,}$", p):
            result["event_code"] = p
        elif re.match(r"^\d{8}$", p):
            result["date_code"] = p
    if "movies" in parts:
        idx = parts.index("movies")
        if idx + 1 < len(parts):
            result["region_slug"] = parts[idx + 1]
    return result

def send_telegram_alert(message: str, retry_forever: bool = True) -> bool:
    """Sends a Telegram markdown message alert. Retries indefinitely if retry_forever is True."""
    if not TELEGRAM_BOT_TOKEN or "YOUR_TELEGRAM_BOT_TOKEN" in TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram Bot Token is not configured. Alert not sent.")
        return False
    if not TELEGRAM_CHAT_ID or "YOUR_TELEGRAM_CHAT_ID" in TELEGRAM_CHAT_ID:
        logger.warning("Telegram Chat ID is not configured. Alert not sent.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    attempt = 1
    retry_delay_seconds = 15

    while True:
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            logger.info("📨 Telegram notification successfully sent!")
            return True
        except Exception as e:
            logger.error(f"⚠️ Failed to send Telegram alert (Attempt {attempt}): {e}")
            if not retry_forever:
                return False
            logger.info(f"Retrying Telegram notification in {retry_delay_seconds} seconds...")
            time.sleep(retry_delay_seconds)
            attempt += 1

def parse_movie_info(data: dict) -> dict:
    """Extracts movie title and language/format subtitle from widgets."""
    info = {"name": None, "language": None}
    
    # Try getting subtitle (language/format) from sticky widgets
    for w in data.get("data", {}).get("topStickyWidgets", []):
        if w.get("type") == "horizontal-text-list":
            for item in w.get("data", []):
                for row in item.get("leftText", {}).get("data", []):
                    for c in row.get("components", []):
                        if "•" in c.get("text", ""):
                            info["language"] = c["text"].strip()
                            
    # Try getting the movie name from bottom sheet format selector subtitle
    bs = data.get("data", {}).get("bottomSheetData", {})
    for w in bs.get("format-selector", {}).get("widgets", []):
        if w.get("type") == "vertical-text-list":
            for d in w.get("data", []):
                if d.get("styleId") == "bottomsheet-subtitle":
                    info["name"] = d.get("text", info["name"])
    return info

def parse_and_validate_response(data: dict, target_date: str) -> tuple[bool, str | None, list[dict]]:
    """
    Parses BMS response to find the returned date and collect showtimes.
    Returns (has_target_date_shows, actual_date_returned, list_of_shows).
    """
    show_widgets = data.get("data", {}).get("showtimeWidgets", [])
    if not show_widgets:
        return False, None, []

    actual_date_returned = None
    has_target_date_shows = False
    shows_list = []

    for widget in show_widgets:
        if widget.get("type") != "groupList":
            continue
        for group in widget.get("data", []):
            if group.get("type") != "venueGroup":
                continue
            for card in group.get("data", []):
                if card.get("type") != "venue-card":
                    continue
                
                venue_name = card.get("additionalData", {}).get("venueName", "Unknown Venue")
                for show in card.get("showtimes", []):
                    sa = show.get("additionalData", {})
                    show_date = str(sa.get("showDateCode") or sa.get("dateCode") or "").strip()
                    
                    if show_date:
                        actual_date_returned = show_date
                        show_time = show.get("title", "")
                        screen_attr = show.get("screenAttr", "")
                        
                        shows_list.append({
                            "venue": venue_name,
                            "time": show_time,
                            "date": show_date,
                            "screen": screen_attr
                        })
                        
                        if show_date == target_date:
                            has_target_date_shows = True

    return has_target_date_shows, actual_date_returned, shows_list

def run_check(args) -> bool:
    """Executes a single availability check sequence."""
    logger.info(f"Checking BookMyShow availability for {args.date_code}...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://in.bookmyshow.com/movies/{args.region_slug}/buytickets/{args.event_code}/{args.date_code}",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "x-app-code": "WEB",
        "x-region-code": args.region_code,
        "x-region-slug": args.region_slug,
        "x-geohash": args.geohash,
        "x-latitude": args.latitude,
        "x-longitude": args.longitude,
        "x-location-selection": "manual",
        "x-lsid": "",
    }

    params = {
        "eventCode": args.event_code,
        "dateCode": args.date_code,
        "isDesktop": "true",
        "regionCode": args.region_code,
        "lat": args.latitude,
        "lon": args.longitude,
        "xLocationShared": "false",
        "memberId": "",
        "lsId": "",
        "subCode": "",
    }

    try:
        resp = requests.get(API_URL, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"BMS API returned non-200 status code: {resp.status_code}")
            return False
        
        try:
            data = resp.json()
        except ValueError:
            logger.warning("BMS API did not return valid JSON content.")
            return False

        has_target_date_shows, actual_date, shows = parse_and_validate_response(data, args.date_code)

        if not actual_date:
            logger.info("ℹ️ No showtimes returned. Date is still closed.")
            return False

        if actual_date != args.date_code:
            logger.info(f"ℹ️ BMS redirected to {actual_date}. Target date {args.date_code} is still closed.")
            return False

        if has_target_date_shows:
            # Extract movie metadata
            movie_info = parse_movie_info(data)
            movie_name = movie_info.get("name") or "Your Selected Movie"
            movie_lang = movie_info.get("language") or "N/A"
            
            logger.info(f"🎉 Success! Active shows verified for target date {args.date_code} ({movie_name} - {movie_lang})!")
            
            # Format a preview of available shows
            shows_summary = []
            for s in shows:
                if s["date"] == args.date_code:
                    screen_str = f" ({s['screen']})" if s['screen'] else ""
                    shows_summary.append(f"• *{s['venue']}* at *{s['time']}*{screen_str}")
            
            shows_text = "\n".join(shows_summary[:10]) # limit to first 10 shows in message
            if len(shows_summary) > 10:
                shows_text += f"\n• ...and {len(shows_summary) - 10} more shows."

            booking_url = f"https://in.bookmyshow.com/movies/{args.region_slug}/buytickets/{args.event_code}/{args.date_code}"
            
            alert_msg = (
                f"🚨 *TICKETS ARE LIVE!*\n\n"
                f"🎬 *Event:* {movie_name}\n"
                f"🗣️ *Language/Format:* {movie_lang}\n"
                f"📍 *Location:* {args.region.capitalize()} ({args.region_code})\n"
                f"📅 *Target Date:* {args.date_code}\n\n"
                f"🎟️ *Available Showtimes Preview:*\n"
                f"{shows_text or '• Direct booking slots available'}\n\n"
                f"🔗 *[Click here to Book Tickets]({booking_url})*"
            )
            # If tickets are found, we retry sending the Telegram alert indefinitely
            send_telegram_alert(alert_msg, retry_forever=True)
            return True

    except requests.exceptions.ConnectionError as e:
        logger.warning(f"⚠️ Internet connection seems to be down or unreachable: {e}. Retrying on next check...")
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during check: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

    return False

def main():
    parser = argparse.ArgumentParser(
        description="BookMyShow Ticket Availability Monitor",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # Generate list of supported regions for help output
    supported_regions_list = "\n".join(f"  - {r}" for r in REGION_MAP.keys())
    region_help_text = f"Target region/location.\nSupported regions:\n{supported_regions_list}"

    # BMS configuration arguments
    parser.add_argument("--url", help="BMS Booking page URL (automatically parses region, event, and target date if present)")
    parser.add_argument("--region", default="hyderabad", help=region_help_text)
    parser.add_argument("--event-code", default="ET00504055", help="BMS Event Code (ignored if --url is specified)")
    parser.add_argument("--date-code", default="20260724", help="Target Date Code YYYYMMDD (ignored if --url contains a date code)")
    parser.add_argument("--region-slug", help="BMS Region Slug (auto-resolved if not specified)")
    parser.add_argument("--region-code", help="BMS Region Code (auto-resolved if not specified)")
    parser.add_argument("--latitude", help="Latitude location coordinate (auto-resolved if not specified)")
    parser.add_argument("--longitude", help="Longitude location coordinate (auto-resolved if not specified)")
    parser.add_argument("--geohash", help="Geohash location value (auto-resolved if not specified)")
    parser.add_argument("--interval", type=int, default=180, help="Check interval in seconds")
    
    # Telegram testing flag
    parser.add_argument("--test-telegram", action="store_true", help="Send a test message to Telegram and exit immediately")

    args = parser.parse_args()

    # Parse URL if supplied
    if args.url:
        parsed_url = parse_bms_url(args.url)
        if parsed_url["event_code"]:
            args.event_code = parsed_url["event_code"]
            logger.info(f"Parsed Event Code from URL: {args.event_code}")
        if parsed_url["date_code"]:
            args.date_code = parsed_url["date_code"]
            logger.info(f"Parsed Date Code from URL: {args.date_code}")
        if parsed_url["region_slug"]:
            args.region = parsed_url["region_slug"]
            logger.info(f"Parsed Region Slug from URL: {args.region}")

    # Resolve region coordinates, slug, code, and geohash
    region_choice = args.region.lower().strip()
    if region_choice not in REGION_MAP:
        logger.error(
            f"Unsupported region: '{args.region}'. "
            f"Please choose from: {', '.join(REGION_MAP.keys())}"
        )
        sys.exit(1)

    r_code, r_slug, lat, lon, geo = REGION_MAP[region_choice]

    # Assign resolved values unless explicitly overridden via CLI
    args.region_code = args.region_code or r_code
    args.region_slug = args.region_slug or r_slug
    args.latitude = args.latitude or lat
    args.longitude = args.longitude or lon
    args.geohash = args.geohash or geo

    if args.test_telegram:
        logger.info("Executing Telegram integration check...")
        test_msg = (
            f"ℹ️ *BMS Monitor Test Alert*\n\n"
            f"If you are reading this, your Telegram integration is working perfectly!\n"
            f"Event code configured: `{args.event_code}`\n"
            f"Target date code configured: `{args.date_code}`\n"
            f"Region slug: `{args.region_slug}`\n"
            f"Region: `{args.region}` (Resolved: Code={args.region_code}, Lat={args.latitude}, Lon={args.longitude}, Geohash={args.geohash})"
        )
        # For the test CLI command, we do not retry forever
        success = send_telegram_alert(test_msg, retry_forever=False)
        if success:
            logger.info("Test message sent successfully.")
            sys.exit(0)
        else:
            logger.error("Failed to send test message.")
            sys.exit(1)

    logger.info(
        f"🚀 Telegram Monitor started.\n"
        f"URL check: {args.url}\n"
        f"Event code: {args.event_code}\n"
        f"Target date: {args.date_code}\n"
        f"Region: {args.region} (Resolved: Code={args.region_code}, Lat={args.latitude}, Lon={args.longitude}, Geohash={args.geohash})\n"
        f"Checking every {args.interval} seconds..."
    )
    try:
        while True:
            if run_check(args):
                logger.info("Execution complete. Terminating script.")
                sys.exit(0)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("\n👋 Monitor stopped gracefully by user (Ctrl+C).")
        sys.exit(0)

if __name__ == "__main__":
    main()
