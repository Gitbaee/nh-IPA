import requests
from credential_store import get_credentials
TOKEN_URL = "https://api.nhplug.com:8443/oauth2/token"

def get_access_token():
    app_key, app_secret = get_credentials()
    data = {"appkey": app_key, "appsecretkey": app_secret, "grant_type": "client_credentials", "scope": "oob"}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(TOKEN_URL, data=data, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()

def get_token():
    return get_access_token().get("access_token")

if __name__ == "__main__":
    result = get_access_token()
    print("Namuh PLUG 인증 성공")
    print("token_type:", result.get("token_type"))
    print("expires_in:", result.get("expires_in"))
    token = result.get("access_token")
    if token:
        print("access_token:", token[:8] + "...")
