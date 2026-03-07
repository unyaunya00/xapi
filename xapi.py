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
    tmp_path = os.path.join("/tmp", unique_name)
    post_img.save(tmp_path)
    expires_at = datetime.utcnow() + timedelta(days=30)
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            'SELECT accesstoken, accesssecret FROM "Apikeys" WHERE sessionid = %s',
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
        if keys and keys[0] and keys[1]:
            return jsonify ({
                "status": "authorized",
                "next": f"/post_tweet?uid={user_id}"
            })
        auth_handler = tweepy.OAuth1UserHandler(CK, CS, callback_url)
        authorize_url = auth_handler.get_authorization_url()
        request_token = auth_handler.request_token["oauth_token"]
        request_secret = auth_handler.request_token["oauth_token_secret"]
        cur.execute(
            '''
            UPDATE "Apikeys"
            SET request_token=%s, request_secret=%s
            WHERE sessionid=%s
            ''',
            (request_token, request_secret, user_id)
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
    oauth_token = request.args.get("oauth_token")
    verifier = request.args.get("oauth_verifier")
    if not oauth_token:
        return {"error": "invalid oauth"}, 400
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT sessionid, request_secret
            FROM "Apikeys"
            WHERE request_token=%s
            ''',
            (oauth_token,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Invalid token or session expired"}, 400
        user_id = row[0]
        request_secret = row[1]
        
        auth_handler = tweepy.OAuth1UserHandler(CK, CS, callback_url)
        auth_handler.request_token = {
            "oauth_token": oauth_token,
            "oauth_token_secret": request_secret
        }
        access_token, access_token_secret = auth_handler.get_access_token(verifier)
        encrypted_token = cipher_suite.encrypt(access_token.encode()).decode()
        encrypted_secret = cipher_suite.encrypt(access_token_secret.encode()).decode()
        cur.execute(
            '''
            UPDATE "Apikeys"
            SET accesstoken=%s,
                accesssecret=%s
            WHERE sessionid=%s
            ''',
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
        raw_access_token = cipher_suite.decrypt(keys[0].encode()).decode()
        raw_access_secret = cipher_suite.decrypt(keys[1].encode()).decode()
        image_path = keys[2]
        text = keys[3]
        
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
    finally:
        cur.close()
        conn.close()

@app.route("/")
def home():
    return {"status": "ok"}
