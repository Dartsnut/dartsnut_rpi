import subprocess

def check_update():
    result = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD..github-release/release'],
        cwd='/home/rpi/dartsnut_rpi',
        check=True,
        stdout=subprocess.PIPE,
        text=True
    )
    commits_ahead = int(result.stdout.strip())
    if commits_ahead > 0:
        return {"action": "check_update", "update": True}
    else:
        return {"action": "check_update", "update": False}

def perform_update():
    try:
        subprocess.run(['git', 'reset', '--hard'], cwd='/home/rpi/dartsnut_rpi', check=True)
        subprocess.run(['git', 'fetch', 'github-release'], cwd='/home/rpi/dartsnut_rpi', check=True)
        subprocess.run(['git', 'reset', '--hard', 'github-release/release'], cwd='/home/rpi/dartsnut_rpi', check=True)
        return {"action": "perform_update", "message": "Update successful"}
    except subprocess.CalledProcessError as e:
        return {"action": "perform_update", "error": f"Update failed: {str(e)}"}