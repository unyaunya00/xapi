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

@app.route("/check_auth", methods=["POST"])
def check_auth():
    user_id = request.form.get("user_id")
    print(f"DEBUG: received user_id: {user_id}")
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
            (user_id, temp_path, post_txt, expires_at)
        )
        conn.commit()
        if keys and keys[0] and keys[1]:
            return {
                "status": "authorized",
                "next": "/post_tweet"
            }
        my_callback_url = f"https://xapi-4s97.onrender.com/callback?uid={user_id}"
        auth_handler = tweepy.OAuth1UserHandler(CK, CS, my_callback_url)
        authorize_url = auth_handler.get_authorization_url()
        return {
            "status": "not_authorized",
            "next": authorize_url
        }
    finally:
        cur.close()
        conn.close()

@app.route("/callback")
def call_back():
    user_id = request.args.get("uid")
    if not user_id:
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
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            'UPDATE "Apikeys" SET accesstoken = %s, accesssecret = %s WHERE sessionid = %s',
            (access_token, access_token_secret, user_id)
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
        return {"error": "not authorized"}, 401
    access_token = keys[0]
    access_secret = keys[1]
    image_path = keys[2]
    text = keys[3]
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)