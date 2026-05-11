#!/usr/bin/env python3
"""
Script to check for git updates and perform them if available.
This script is designed to be run as a cron job.
"""

import sys
import os

# Add the project directory to the path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from python_websocket.git_operations import check_update, perform_update

def main():
    """Main function to check for updates and perform them if needed."""
    try:
        check_result = check_update()
        if "error" in check_result:
            print(f"Error checking for updates: {check_result.get('error', 'Unknown error')}")
            return 1

        latest_version = check_result.get("latest_version")
        if not latest_version or "current_version" not in check_result:
            print("Error: Could not determine current or latest version")
            return 1
        current_version = check_result["current_version"]
        if "needs_update" not in check_result:
            print("Error: Incomplete update check response")
            return 1

        print(f"Current version: {current_version}")
        print(f"Latest version: {latest_version}")

        if not check_result["needs_update"]:
            print("No update available. System is up to date.")
            return 0

        print(f"Update available: {current_version} -> {latest_version}")
        print("Performing update...")
        
        # Perform the update
        update_result = perform_update()
        if "error" in update_result:
            print(f"Error performing update: {update_result.get('error', 'Unknown error')}")
            return 1
        
        print(f"Update successful: {update_result.get('message', 'Update completed')}")
        return 0
        
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
