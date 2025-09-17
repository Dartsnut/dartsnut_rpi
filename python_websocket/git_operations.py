import subprocess

def get_version():
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--abbrev=0'],
            cwd='/home/rpi/dartsnut_rpi',
            check=True,
            stdout=subprocess.PIPE,
            text=True
        )
        version_tag = result.stdout.strip()
        return {"action": "get_version", "version": version_tag}
    except subprocess.CalledProcessError as e:
        return {"action": "get_version", "error": f"Failed to get version: {str(e)}"}

def check_update():
    try:
        subprocess.run(['git', 'fetch', 'origin'], cwd='/home/rpi/dartsnut_rpi', check=True)
        result = subprocess.run(
            ['git', 'describe', '--tags', 'origin/release', '--abbrev=0'],
            cwd='/home/rpi/dartsnut_rpi',
            check=True,
            stdout=subprocess.PIPE,
            text=True
        )
        latest_tag = result.stdout.strip()
        return {"action": "check_update", "latest_version": latest_tag}
    except subprocess.CalledProcessError as e:
        return {"action": "check_update", "error": f"Failed to check for updates: {str(e)}"}

def perform_update():
    try:
        subprocess.run(['git', 'reset', '--hard'], cwd='/home/rpi/dartsnut_rpi', check=True)
        subprocess.run(['git', 'fetch', 'origin'], cwd='/home/rpi/dartsnut_rpi', check=True)
        subprocess.run(['git', 'reset', '--hard', 'origin/release'], cwd='/home/rpi/dartsnut_rpi', check=True)
        return {"action": "perform_update", "message": "Update successful"}
    except subprocess.CalledProcessError as e:
        return {"action": "perform_update", "error": f"Update failed: {str(e)}"}