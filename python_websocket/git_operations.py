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
        # Save current commit hash
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd='/home/rpi/dartsnut_rpi',
            check=True,
            stdout=subprocess.PIPE,
            text=True
        )
        old_commit = result.stdout.strip()

        subprocess.run(['git', 'reset', '--hard'], cwd='/home/rpi/dartsnut_rpi', check=True)
        subprocess.run(['git', 'fetch', 'origin'], cwd='/home/rpi/dartsnut_rpi', check=True)
        subprocess.run(['git', 'reset', '--hard', 'origin/release'], cwd='/home/rpi/dartsnut_rpi', check=True)

        # Check if setup.sh has changed
        diff_result = subprocess.run(
            ['git', 'diff', '--name-only', old_commit, 'HEAD', '--', 'setup.sh'],
            cwd='/home/rpi/dartsnut_rpi',
            stdout=subprocess.PIPE,
            text=True
        )
        
        if 'setup.sh' not in diff_result.stdout:
            return {"action": "perform_update", "message": "Update successful"}
        else:
            subprocess.run(['sudo', './setup.sh'], cwd='/home/rpi/dartsnut_rpi', check=True)
            return {"action": "perform_update", "message": "Update successful"}
    except subprocess.CalledProcessError as e:
        # Rollback to old commit
        try:
            subprocess.run(['git', 'reset', '--hard', old_commit], cwd='/home/rpi/dartsnut_rpi', check=True)
            return {"action": "perform_update", "error": f"Update failed: {str(e)}. Rolled back to previous version."}
        except subprocess.CalledProcessError as rollback_error:
            return {"action": "perform_update", "error": f"Update failed: {str(e)}. Rollback also failed: {str(rollback_error)}"}