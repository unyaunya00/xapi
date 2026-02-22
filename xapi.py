# python/twitter_post.py
import tweepy
from flask import Flask, request, redirect, session
import psycopg2
from dotenv import load_dotenv
from datetime import datetime, timedelta
from urllib.parse import quote
from flask_cors import CORS
import os
import uuid

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

CK = os.getenv("CK")
CS = os.getenv("CS")
DATABASE_URL = os.getenv("DATABASE_URL")
callback_url = "https://xapi-4s97.onrender.com/callback"

@app.before_request
def ensure_session():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())

@app.route("/check_auth", methods=["POST"])
def check_auth():
    session_id = session.get("session_id")
    if not session_id:
        return {"error": "no session"}, 401
    session["post_img"] = None
    session["post_txt"] = ""
    post_img = request.files.get("image")
    post_txt = request.form.get("text")
    unique_name = f"{uuid.uuid4()}_{post_img.filename}"
    temp_path = os.path.join("/tmp", unique_name)
    post_img.save(temp_path)
    session["post_img"] = temp_path
    session["post_txt"] = post_txt

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            'SELECT accesstoken, accesssecret FROM "Apikeys" WHERE sessionid = %s', 
            (session_id, )
        )
        keys = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if keys:
        return {
            "status": "authorized",
            "next": f"/post_tweet"
        }
    else:
        auth_handler = tweepy.OAuth1UserHandler(CK, CS, callback_url)
        authorize_url = auth_handler.get_authorization_url()
        return {
            "status": "not_authorized",
            "next": authorize_url
        }

@app.route("/callback")
def call_back():
    session_id = session.get("session_id")
    if not session_id:
        return {"error": "no session"}, 401
    verifier = request.args.get("oauth_verifier")
    oauth_token = request.args.get("oauth_token")
    auth_handler = tweepy.OAuth1UserHandler(CK, CS, callback_url)
    auth_handler.request_token = {
        'oauth_token': oauth_token,
        'oauth_token_secret': verifier
    }
    try:
        access_token, access_token_secret = auth_handler.get_access_token(verifier)
        expires_at = datetime.utcnow() + timedelta(days=30)
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            'UPDATE "Apikeys" SET accesstoken = %s, accesssecret = %s, expires_at = %s WHERE sessionid = %s',
            (access_token, access_token_secret, expires_at, session_id)
        )
        if cur.rowcount == 0:
            cur.execute(
                '''
                INSERT INTO "Apikeys" (sessionid, accesstoken, accesssecret, expires_at)
                VALUES (%s, %s, %s, %s)
                ''',
                (session_id, access_token, access_token_secret, expires_at)
            )
        conn.commit()
        return redirect("/post_tweet")
    except FileNotFoundError:
        return "投稿情報の有効期限が切れたか、見つかりません。", 400
    finally:
        cur.close()
        conn.close()

@app.route("/post_tweet")
def post_tweet():
    session_id = session.get("session_id")
    if not session_id:
        return {"error": "no session"}, 401
    image_path = session.get("post_img")
    if not os.path.exists(image_path):
        return {"error": "file not found"}, 400
    text = session.get("post_txt")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        'SELECT accesstoken, accesssecret FROM "Apikeys" WHERE sessionid = %s', 
        (session_id, )
    )
    keys = cur.fetchone()
    if not keys:
        return {"error": "not authorized"}, 401
    access_token = keys[0]
    access_secret = keys[1]
    cur.close()
    conn.close()
    
    auth = tweepy.OAuth1UserHandler(
        CK, CS,
        access_token,
        access_secret
    )
    api_v1 = tweepy.API(auth)
    media = api_v1.media_upload(image_path)
    client = tweepy.Client(
        consumer_key=CK,
        consumer_secret=CS,
        access_token=access_token,
        access_token_secret=access_secret
    )
    client.create_tweet(
        text=text,
        media_ids=[media.media_id]
    )
    return redirect("https://x.com/")

if __name__ == "__main__":
    app.run(port=8024, debug=False, use_reloader=False)