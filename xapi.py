# python/twitter_post.py
from dotenv import load_dotenv
load_dotenv()

import tweepy
from flask import Flask, request, redirect, jsonify
import psycopg2
from cryptography.fernet import Fernet
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from flask_cors import CORS
from requests_oauthlib import OAuth2Session
import os
import uuid
from io import BytesIO

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

CK = os.getenv("CK")
CS = os.getenv("CS")
DATABASE_URL = os.getenv("DATABASE_URL")
ENCRYPT_KEY = os.getenv("ENCRYPT_KEY")
if not ENCRYPT_KEY:
    raise ValueError("ENCRYPT_KEY is required")
cipher_suite = Fernet(ENCRYPT_KEY.encode() if isinstance(ENCRYPT_KEY, str) else ENCRYPT_KEY)
callback_url = "https://xapi-4s97.onrender.com/callback"

AUTHORIZATION_BASE_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"

SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "offline.access",
    "media.write"
]
SCOPE_STR = " ".join(SCOPES)
SCOPES_LIST = SCOPES


@app.route("/check_auth", methods=["POST"])
def check_auth():
    user_id = request.form.get("user_id")
    post_img = request.files.get("image")
    post_txt = request.form.get("text") or ""

    post_img_data = None
    post_img_path = None
    if post_img and post_img.filename:
        post_img_data = post_img.read()
        post_img_path = post_img.filename 

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
            INSERT INTO "Apikeys" (sessionid, post_img, post_txt, expires_at, post_img_data)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sessionid)
            DO UPDATE SET
                post_img = EXCLUDED.post_img,
                post_txt = EXCLUDED.post_txt,
                expires_at = EXCLUDED.expires_at,
                post_img_data = EXCLUDED.post_img_data
            ''',
            (user_id, post_img_path, post_txt, expires_at, psycopg2.Binary(post_img_data) if post_img_data else None)
        )
        conn.commit()

        if keys and keys[0]:
            return jsonify({
                "status": "authorized",
                "next": f"/post_tweet?uid={user_id}"
            })

        oauth = OAuth2Session(
            client_id=CLIENT_ID,
            redirect_uri=callback_url,
            scope=SCOPES_LIST,
            pkce="S256",
        )
        auth_url, _ = oauth.authorization_url(
            AUTHORIZATION_BASE_URL,
            state=user_id,
        )
        code_verifier = getattr(oauth, "_code_verifier", None)
        if not code_verifier:
            return jsonify({"error": "PKCE code_verifier could not be obtained"}), 500

        cur.execute(
            '''
            UPDATE "Apikeys"
            SET oauth_state = %s, code_verifier = %s
            WHERE sessionid = %s
            ''',
            (user_id, code_verifier, user_id)
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
    parsed = urlparse(request.url)
    qs = parse_qs(parsed.query)
    state_list = qs.get("state")
    if not state_list:
        return {"error": "Missing state parameter"}, 400
    state = state_list[0] if isinstance(state_list, list) else state_list

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT sessionid, code_verifier
            FROM "Apikeys"
            WHERE oauth_state = %s AND code_verifier IS NOT NULL
            ''',
            (state,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Invalid state or session expired"}, 400

        user_id, code_verifier = row[0], row[1]

        oauth = OAuth2Session(
            client_id=CLIENT_ID,
            redirect_uri=callback_url,
            scope=SCOPES_LIST,
            pkce="S256",
        )
        oauth._code_verifier = code_verifier

        token = oauth.fetch_token(
            TOKEN_URL,
            authorization_response=request.url,
            client_secret=CLIENT_SECRET,
            include_client_id=True,
        )
        access_token = token.get("access_token")
        if not access_token:
            return {"error": "Failed to obtain access token"}, 500

        encrypted_token = cipher_suite.encrypt(access_token.encode()).decode()

        cur.execute(
            '''
            UPDATE "Apikeys"
            SET accesstoken = %s, code_verifier = NULL, oauth_state = NULL
            WHERE sessionid = %s
            ''',
            (encrypted_token, user_id)
        )
        conn.commit()

        return redirect(f"/post_tweet?uid={user_id}")
    except Exception as e:
        return {"error": str(e)}, 500
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
            SELECT accesstoken, accesssecret, post_img, post_txt, post_img_data
            FROM "Apikeys"
            WHERE sessionid = %s
            ''',
            (user_id,)
        )
        keys = cur.fetchone()
        if not keys:
            return {"error": "not authorized"}, 401
        access_token_enc = keys[0]
        image_path = keys[2]
        text = keys[3] or ""
        post_img_data = keys[4] if len(keys) > 4 else None

        if not access_token_enc:
            return {"error": "not authorized"}, 401
        access_token = cipher_suite.decrypt(access_token_enc.encode()).decode()

        client = tweepy.Client(access_token=access_token)

        media_ids = []
        image_to_upload = None
        if post_img_data:
            image_to_upload = BytesIO(post_img_data)
        elif image_path and os.path.isfile(image_path):
            image_to_upload = image_path

        if image_to_upload:
            auth_v1 = tweepy.OAuth1UserHandler(CK, CS)
            api_v1 = tweepy.API(auth_v1)
            try:
                if isinstance(image_to_upload, BytesIO):
                    image_to_upload.seek(0)
                    media = api_v1.media_upload(filename="image", file=image_to_upload)
                else:
                    media = api_v1.media_upload(image_to_upload)
                media_ids = [media.media_id]
            except Exception:
                pass

        if media_ids:
            client.create_tweet(text=text, media_ids=media_ids)
        else:
            client.create_tweet(text=text)

        return redirect("https://x.com/")
    finally:
        cur.close()
        conn.close()


@app.route("/")
def home():
    return {"status": "ok"}
