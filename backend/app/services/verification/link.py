import requests

def verify_link(url):
    try:
        r = requests.get(url, timeout=5)
        return "verified" if r.status_code == 200 else "rejected"
    except:
        return "rejected"