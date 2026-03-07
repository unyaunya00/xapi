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
import secrets

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
        cur.execute('SELECT accesstoken FROM "Apikeys" WHERE sessionid = %s', (user_id,))
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
            return jsonify({"status": "authorized", "next": f"/post_tweet?uid={user_id}"})
        state = secrets.token_urlsafe(32)
        cur.execute(
            'UPDATE "Apikeys" SET request_token = %s WHERE sessionid = %s',
            (state, user_id)
        )
        conn.commit()
        oauth2_handler = tweepy.OAuth2UserHandler(
            client_id=CLIENT_ID,
            redirect_uri=callback_url,
            scope=["tweet.read", "tweet.write", "users.read", "offline.access"],
            client_secret=CLIENT_SECRET
        )
        authorize_url = oauth2_handler.get_authorization_url(state=state)
        current_state = oauth2_handler.state
        cur.execute(
            'UPDATE "Apikeys" SET request_token = %s WHERE sessionid = %s',
            (current_state, user_id)
        )
        return jsonify ({
            "status": "not_authorized",
            "next": authorize_url
        })
    finally:
        cur.close()
        conn.close()

@app.route("/callback")
def call_back():
    state_from_twitter = request.args.get("state")
    code = request.args.get("code")
    if not state_from_twitter or not code:
        return "パラメータが足りません。", 400
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            'SELECT sessionid FROM "Apikeys" WHERE request_token = %s AND expires_at > NOW()',
            (state_from_twitter,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Invalid token or session expired"}, 400
        user_id = row[0]
        
        oauth2_handler = tweepy.OAuth2UserHandler(
            client_id=CLIENT_ID,
            redirect_uri=callback_url,
            scope=["tweet.read", "tweet.write", "users.read", "offline.access", "media.write"],
            client_secret=CLIENT_SECRET
        )
        token = oauth2_handler.fetch_token(
            authorization_response=request.url
        )
        access_token = token.get("access_token")
        encrypted_token = cipher_suite.encrypt(access_token.encode()).decode()
        cur.execute(
            'UPDATE "Apikeys" SET accesstoken = %s, request_token = NULL WHERE sessionid = %s',
            (encrypted_token, user_id)
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
        'SELECT accesstoken, accesssecret, post_img, post_txt FROM "Apikeys" WHERE sessionid = %s', 
        (user_id, )
    )
    keys = cur.fetchone()
    if not keys:
        cur.close()
        conn.close()
        return {"error": "not authorized"}, 401
    raw_access_token = cipher_suite.decrypt(keys[0].encode()).decode()
    is_oauth2 = keys[1] is None
    if not is_oauth2:
        raw_access_secret = cipher_suite.decrypt(keys[1].encode()).decode()
    image_path = keys[2]
    text = keys[3]
    cur.close()
    conn.close()
    if is_oauth2:
        client = tweepy.Client(access_token=raw_access_token)
    else:
        client = tweepy.Client(
            consumer_key=CK,
            consumer_secret=CS,
            access_token=raw_access_token,
            access_token_secret=raw_access_secret
        )
    api_v1 = tweepy.API(client)
    media = api_v1.media_upload(image_path)
    if is_oauth2:
        client = tweepy.Client(user_auth=False, access_token=raw_access_token)
    else:
        client = tweepy.Client(
            consumer_key=CK, consumer_secret=CS,
            access_token=raw_access_token, access_token_secret=raw_access_secret
        )

    client.create_tweet(text=text, media_ids=[media.media_id])
    if os.path.exists(image_path):
        os.remove(image_path)

    return redirect("https://x.com/home")
