# AppHub Version Configuration
# Update this file when you release a new version of AppHub

VERSION = "10.0.0"
BUILD_NUMBER = 4010
# Force all builds older than the current release to update.
MIN_SUPPORTED_BUILD = BUILD_NUMBER
RELEASE_DATE = "2026-05-26"

# File Information
DOWNLOAD_URLS = {
    "arm64-v8a": "http://apphubx.store/apphub/app/app-arm64-v8a-release.apk",
    "armeabi-v7a": "http://apphubx.store/apphub/app/app-armeabi-v7a-release.apk",
    "x86": "",
    "x86_64": "http://apphubx.store/apphub/app/app-x86_64-release.apk",
    "universal": ""
}
DOWNLOAD_SIZES = {
    "arm64-v8a": 23000000,
    "armeabi-v7a": 22000000,
    "x86": 23000000,
    "x86_64": 23000000,
    "universal": 40000000
}
DOWNLOAD_URL = DOWNLOAD_URLS["universal"]
APK_HASH = ""  # Example SHA-256 Hash for download integrity verification
SIZE_BYTES = DOWNLOAD_SIZES["universal"]

# Update Enforcement
IS_MANDATORY = False  # If True, prompts an update regardless of MIN_SUPPORTED_BUILD

# Telegram Support
TELEGRAM_CHANNEL = "https://t.me/+IDEuHZyD9lc5Y2Jl"

# Changelog Details
CHANGELOG_TITLE = "🎉 What's New in v10.0.0"
CHANGELOG = """
✨ Major Features
• Updated package name and signing key.
• Fixed security vulnerabilities.
• Added Google and Email login system.
• Added 20+ new websites (Total: 64+).

🛠️ Bug Fixes & Enhancements
• Fixed Kamababa, Dasimms2, YouPorn, and XHamster.
• Many more not on the list

⚠️ CRITICAL INSTALLATION NOTE
• Due to the new package name and security key, you MUST 
uninstall the older version from your device
• If you experience any coin loss while moving away from 
the local guest login system, please contact us 
immediately so we can help you recover them.
"""
