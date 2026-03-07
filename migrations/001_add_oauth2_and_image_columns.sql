-- Apikeys に OAuth2 用および画像永続化用カラムを追加
-- 実行: psql $DATABASE_URL -f migrations/001_add_oauth2_and_image_columns.sql

ALTER TABLE "Apikeys"
  ADD COLUMN IF NOT EXISTS oauth_state TEXT,
  ADD COLUMN IF NOT EXISTS code_verifier TEXT,
  ADD COLUMN IF NOT EXISTS post_img_data BYTEA;

-- 既存の post_img はパス用のため残す（post_img_data を優先して使用する）
