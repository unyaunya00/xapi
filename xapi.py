# python/twitter_post.py
import tweepy
from flask import Flask,request,redirect
import json
from urllib.parse import quote
from flask_cors import CORS
import os
import sys
import threading

app = Flask(__name__)
CORS(app)

# CK = "bTXf57wSznD0XClwo4awdRcHl"
# CS = "8TykaNTi6yEyHIXivipACAM2SsxLmSNVECH3i1OBwerJxsiQey"
auth_handler = None
authorize_url = ""
is_authorize = False
# callback_url = "http://localhost:8024/callback"

def watch_stdin():
    try:
        sys.stdin.read()
    except EOFError:
        pass
    os._exit(0)

@app.route("/check_auth")
def check_auth():
    global auth_handler, authorize_url
    raw_img_path = request.args.get("img")
    post_text = request.args.get("text")
    if not is_authorize:
        auth_handler = tweepy.OAuth1UserHandler(CK, CS, callback_url)
        authorize_url = auth_handler.get_authorization_url()
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump({"img": raw_img_path, "text": post_text}, f)
    if is_authorize:
        query = f"?img={quote(raw_img_path)}&text={quote(post_text)}"
        return {
            "status": "authorized",
            "next": f"/post_tweet{query}"
        }
    return {
        "status": "not authorized",
        "next": authorize_url
    }

@app.route("/callback")
def call_back():
    global is_authorize, auth_handler
    verifier = request.args.get("oauth_verifier")
    oauth_token = request.args.get("oauth_token")
    if auth_handler is None:
        auth_handler = tweepy.OAuth1UserHandler(CK, CS, callback_url)
        auth_handler.request_token = {
            'oauth_token': oauth_token,
            'oauth_token_secret': verifier
        }
    try:
        access_token, access_token_secret = auth_handler.get_access_token(verifier)
        is_authorize = True
        auth_state["access_token"] = access_token
        auth_state["access_secret"] = access_token_secret
        data = {
            "access_token": access_token,
            "access_secret": access_token_secret
        }
        with open(TOKEN_FILE, "w", encoding='utf-8') as file:
            json.dump(data, file, indent=1)
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
        img = pending.get("img")
        text = pending.get("text")
        query = f"?img={quote(img)}&text={quote(text)}"
        return redirect(f"/post_tweet{query}")
    except FileNotFoundError:
        return "投稿情報の有効期限が切れたか、見つかりません。", 400

@app.route("/post_tweet")
def post_tweet():
    img_path = request.args.get("img")
    text = request.args.get("text")
    if not img_path:
        print("Error: img_path is missing in request!")
        return "Missing path", 400
    auth = tweepy.OAuth1UserHandler(
        CK, CS,
        auth_state["access_token"],
        auth_state["access_secret"]
    )
    api_v1 = tweepy.API(auth)
    media = api_v1.media_upload(img_path)
    client = tweepy.Client(
        consumer_key=CK,
        consumer_secret=CS,
        access_token=auth_state["access_token"],
        access_token_secret=auth_state["access_secret"]
    )
    client.create_tweet(
        text=text,
        media_ids=[media.media_id]
    )
    return redirect("https://x.com/")

def start_flask():
    threading.Thread(target=watch_stdin, daemon=True).start()
    global is_authorize, auth_state
    try:
        with open(TOKEN_FILE, 'r', encoding='utf-8') as file:
            data = json.load(file)
            if data.get("access_token") and data.get("access_secret"):
                auth_state["access_token"] = data.get("access_token")
                auth_state["access_secret"] = data.get("access_secret")
                is_authorize = True
    except FileNotFoundError:
        pass
    app.run(port=8024, debug=False, use_reloader=False)

if __name__ == "__main__":
    start_flask()