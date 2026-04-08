import requests

def verify_github(repo_url):
    try:
        response = requests.get(repo_url)
        if response.status_code == 200:
            return "verified"
        return "rejected"
    except:
        return "rejected"