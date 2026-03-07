# python/twitter_post.py
import tweepy
from tweepy import OAuth2UserHandler
from flask import Flask, request, redirect, jsonify
import psycopg2
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from datetime import datetime, timedelta
from urllib.parse import quote
from flask_cors import CORS
import os
import uuid

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

CK = os.getenv("CK")
CS = os.getenv("CS")
DATABASE_URL = os.getenv("DATABASE_URL")
ENCRYPT_KEY = os.getenv("ENCRYPT_KEY").encode()
cipher_suite = Fernet(ENCRYPT_KEY)
callback_url = "https://xapi-4s97.onrender.com/callback"

SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "offline.access",
    "media.write"
]

@app.route("/check_auth", methods=["POST"])
def check_auth():
    user_id = request.form.get("user_id")
    post_img = request.files.get("image")
    post_txt = request.form.get("text")

    unique_name = f"{uuid.uuid4()}_{post_img.filename}"
    tmp_path = os.path.join("/tmp", unique_name)
    post_img.save(tmp_path)

    expires_at = datetime.utcnow() + timedelta(days=30)
    
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            'SELECT accesstoken FROM "Apikeys" WHERE sessionid = %s',
            (user_id,)
        )
        keys = cur.fetchone()

        cur.execute(
            '''
            INSERT INTO "Apikeys" (sessionid, post_img, post_txt, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (sessionid)
            DO UPDATE SET
                post_img = EXCLUDED.post_img,
                post_txt = EXCLUDED.post_txt,
                expires_at = EXCLUDED.expires_at
            ''',
            (user_id, tmp_path, post_txt, expires_at)
        )
        conn.commit()
        if keys and keys[0]:
            return jsonify ({
                "status": "authorized",
                "next": f"/post_tweet?uid={user_id}"
            })
        oauth = OAuth2UserHandler(
            client_id=CLIENT_ID,
            redirect_uri=callback_url,
            scope=SCOPES
        )

        auth_url = oauth.get_authorization_url()

        return jsonify({
            "status": "not_authorized",
            "next": auth_url
        })
    finally:
        cur.close()
        conn.close()

@app.route("/callback")
def call_back():
    code = request.args.get("code")
    if not code:
        return {"error": "invalid code"}, 400
    oauth = OAuth2UserHandler(
        client_id=CLIENT_ID,
        redirect_uri=callback_url,
        scope=SCOPES
    )

    token = oauth.fetch_token(
        code=code,
        client_secret=CLIENT_SECRET
    )

    access_token = token["access_token"]

    encrypted_token = cipher_suite.encrypt(
        access_token.encode()
    ).decode()
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT sessionid, request_secret
            FROM "Apikeys"
            WHERE request_token=%s
            ''',
            (encrypted_token,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Invalid token or session expired"}, 400
        row = cur.fetchone()

        if not row:
            return {"error": "session not found"}, 400

        user_id = row[0]

        conn.commit()

        return redirect(f"/post_tweet?uid={user_id}")
    except FileNotFoundError:
        return "投稿情報の有効期限が切れたか、見つかりません。", 400
    finally:
        cur.close()
        conn.close()

@app.route("/post_tweet")
def post_tweet():
    user_id = request.args.get("uid")
    if not user_id:
        return {"error": "no session"}, 401
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT accesstoken, accesssecret, post_img, post_txt
            FROM "Apikeys"
            WHERE sessionid=%s
            ''',
            (user_id,)
        )
        keys = cur.fetchone()
        if not keys:
            return {"error": "not authorized"}, 401
        access_token = cipher_suite.decrypt(
            keys[0].encode()
        ).decode()
        image_path = keys[1]
        text = keys[2]
        
        auth = tweepy.OAuth1UserHandler(
            CK, CS
        )

        api_v1 = tweepy.API(auth)

        media = api_v1.media_upload(image_path)

        # OAuth2 for tweet
        client = tweepy.Client(
            access_token=access_token
        )

        client.create_tweet(
            text=text,
            media_ids=[media.media_id]
        )

        return redirect("https://x.com/")
    finally:
        cur.close()
        conn.close()

@app.route("/")
def home():
    return {"status": "ok"}
