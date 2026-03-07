# python/twitter_post.py
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

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

CK = os.getenv("CK")
CS = os.getenv("CS")
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
            'SELECT access_token, access_secret FROM "Apikeys" WHERE session_id = %s',
            (user_id,)
        )
        keys = cur.fetchone()

        cur.execute(
            '''
            INSERT INTO "Apikeys" (session_id, post_img, post_txt, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (session_id)
            DO UPDATE SET
                post_img = EXCLUDED.post_img,
                post_txt = EXCLUDED.post_txt,
                expires_at = EXCLUDED.expires_at
            ''',
            (user_id, temp_path, post_txt, expires_at)
        )
        conn.commit()
        if keys and keys[0] and keys[1]:
            return jsonify ({
                "status": "authorized",
                "next": f"/post_tweet?uid={user_id}"
            })
        auth = tweepy.OAuth1UserHandler(CK, CS, callback_url)
        auth_url = auth.get_authorization_url()
        token = auth.request_token["oauth_token"]
        token_secret = auth.request_token["oauth_token_secret"]
        cur.execute(
            """
            UPDATE twitter_sessions
            SET request_token=%s,
                request_token_secret=%s
            WHERE session_id=%s
            """,
            (token, token_secret, user_id)
        )
        conn.commit()
        return jsonify({
            "status": "not_authorized",
            "next": auth_url
        })
    finally:
        cur.close()
        conn.close()

@app.route("/callback")
def call_back():
    oauth_token = request.args.get("oauth_token")
    verifier = request.args.get("oauth_verifier")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT session_id, request_token_secret
        FROM twitter_sessions
        WHERE request_token=%s
        """,
        (oauth_token,)
    )
    row = cur.fetchone()
    if not row:
        return {"error": "Invalid token or session expired"}, 400
    user_id, token_secret = row
    
    auth_handler = tweepy.OAuth1UserHandler(CK, CS, callback_url)
    auth_handler.request_token = {
        "oauth_token": oauth_token,
        "oauth_token_secret": token_secret
    }
    try:
        access_token, access_token_secret = auth_handler.get_access_token(verifier)
        encrypted_token = cipher_suite.encrypt(access_token.encode()).decode()
        encrypted_secret = cipher_suite.encrypt(access_token_secret.encode()).decode()
        cur.execute(
            'UPDATE "Apikeys" SET accesstoken = %s, accesssecret = %s WHERE session_id = %s',
            (encrypted_token, encrypted_secret, user_id)
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
        """
        SELECT access_token, access_secret, post_text, image_path
        FROM twitter_sessions
        WHERE session_id=%s
        """,
        (user_id,)
    )
    keys = cur.fetchone()
    if not keys:
        return {"error": "not authorized"}, 401
    raw_access_token = cipher_suite.decrypt(keys[0].encode()).decode()
    raw_access_secret = cipher_suite.decrypt(keys[1].encode()).decode()
    image_path = keys[2]
    text = keys[3]
    cur.close()
    conn.close()
    
    auth = tweepy.OAuth1UserHandler(
        CK, CS,
        raw_access_token,
        raw_access_secret
    )
    api_v1 = tweepy.API(auth)
    media = api_v1.media_upload(image_path)
    client = tweepy.Client(
        consumer_key=CK,
        consumer_secret=CS,
        access_token=raw_access_token,
        access_token_secret=raw_access_secret
    )
    client.create_tweet(
        text=text,
        media_ids=[media.media_id]
    )
    return redirect("https://x.com/")
