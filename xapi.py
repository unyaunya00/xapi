# python/twitter_post.py
from sqlite3 import Cursor
import tweepy
from flask import Flask, request, redirect, jsonify
import psycopg2
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from datetime import datetime, timedelta
from urllib.parse import quote
from flask_cors import CORS
import os
import uuid
import requests

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

CK = os.getenv("CK")
CS = os.getenv("CS")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")
ENCRYPT_KEY = os.getenv("ENCRYPT_KEY").encode()
cipher_suite = Fernet(ENCRYPT_KEY)
callback_url = "https://xapi-4s97.onrender.com/callback"

@app.route("/check_auth", methods=["POST"])
def check_auth():
    user_id = request.form.get("user_id")
    post_img = request.files.get("image")
    post_txt = request.form.get("text")
    unique_name = f"{uuid.uuid4()}_{post_img.filename}"
    temp_path = os.path.join("/tmp", unique_name)
    post_img.save(temp_path)
    expires_at = datetime.utcnow() + timedelta(days=30)
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
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
            (user_id, temp_path, post_txt, expires_at)
        )
        conn.commit()
        if keys and keys[0]:
            return jsonify ({
                "status": "authorized",
                "next": f"/post_tweet?uid={user_id}"
            })
        auth_handler = tweepy.OAuth2UserHandler(
            client_id=CLIENT_ID,
            redirect_uri=callback_url,
            scope=[
                "tweet.read",
                "tweet.write",
                "users.read",
                "offline.access",
                "media.write"
            ],
            client_secret=CLIENT_SECRET
        )
        authorize_url = auth_handler.get_authorization_url()
        state = auth_handler.state()
        cur.execute(
            'UPDATE "Apikeys" SET state = %s WHERE sessionid = %s',        
            (state, user_id)
        )
        conn.commit()
        return jsonify ({
            "status": "not_authorized",
            "next": authorize_url
        })
    finally:
        cur.close()
        conn.close()

@app.route("/callback")
def call_back():
    state = request.args.get('state')
    code = request.args.get("code")
    if not state or not code:
        return {"error": "missing parameters"}, 400

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT sessionid
            FROM "Apikeys"
            WHERE state = %s
            ''',
            (state,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": "invalid state"}, 400
        
        user_id = row[0]
        auth_handler = tweepy.OAuth2UserHandler(
            client_id=CLIENT_ID,
            redirect_uri=callback_url,
            scope=[
                "tweet.read",
                "tweet.write",
                "users.read",
                "offline.access"
            ],
            client_secret=CLIENT_SECRET
        )
        response = auth_handler.fetch_token(
            request.url
        )
        access_token = response["access_token"]
    
        cur.execute(
            '''
            UPDATE "Apikeys"
            SET accesstoken = %s,
                state = NULL
            WHERE sessionid = %s
            ''',
            (access_token, user_id)
        )
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
    cur.execute(
        'SELECT accesstoken, post_img, post_txt FROM "Apikeys" WHERE sessionid = %s', 
        (user_id, )
    )
    keys = cur.fetchone()
    if not keys:
        return {"error": "not authorized"}, 401
    raw_access_token = cipher_suite.decrypt(keys[0].encode()).decode()
    image_path = keys[1]
    text = keys[2]
    cur.close()
    conn.close()
    
    headers = {
        "Authorization": f"Bearer {raw_access_token}"
    }

    files = {
        "media": open(image_path, "rb")
    }

    res = requests.post(
        "https://upload.twitter.com/1.1/media/upload.json",
        headers=headers,
        files=files
    )

    media_id = res.json()["media_id_string"]

    client = tweepy.Client(access_token=raw_access_token)

    client.create_tweet(
        text=text,
        media_ids=[media_id]
    )

    return redirect("https://x.com/")

@app.route("/")
def home():
    return redirect("OK")