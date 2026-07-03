#!/usr/bin/env python3
"""Backfill 附件縮圖:對既有 thumb_key IS NULL 的圖片附件補產縮圖。
**不覆寫既有原圖**。冪等(已有 thumb_key 的跳過)。

用法(prod / 本機):
    R2_ENDPOINT_URL=... R2_BUCKET_NAME=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
    DATABASE_URL=postgresql://... python scripts/backfill_thumbnails.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from image_utils import process_upload_image, thumb_key_for, PROCESSABLE_TYPES

TABLES = ("note_attachments", "private_attachments", "announcement_attachments")


def backfill_one(content_type, object_key, get_bytes, put_bytes):
    """回 thumb_key(已上傳)或 None(非圖/失敗)。純邏輯,IO 由 callback 注入。"""
    if content_type not in PROCESSABLE_TYPES:
        return None
    raw = get_bytes(object_key)
    _main, thumb = process_upload_image(raw, content_type)
    if thumb is None:
        return None
    tk = thumb_key_for(object_key)
    put_bytes(tk, thumb)
    return tk


def main():
    import psycopg2
    import boto3

    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

    def get_bytes(key):
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    def put_bytes(key, body):
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="image/jpeg")

    conn = psycopg2.connect(dsn)
    total = done = 0
    for tbl in TABLES:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, object_key, content_type FROM {tbl} "
            f"WHERE thumb_key IS NULL AND content_type LIKE 'image/%'"
        )
        rows = cur.fetchall()
        print(f"[{tbl}] {len(rows)} 筆待處理", flush=True)
        for aid, object_key, content_type in rows:
            total += 1
            try:
                tk = backfill_one(content_type, object_key, get_bytes, put_bytes)
                if tk:
                    up = conn.cursor()
                    up.execute(f"UPDATE {tbl} SET thumb_key=%s WHERE id=%s", (tk, aid))
                    conn.commit()
                    done += 1
                    if done % 20 == 0:
                        print(f"  ...{done} 完成", flush=True)
            except Exception as e:
                conn.rollback()
                print(f"  ! {tbl}#{aid} {object_key}: {e}", flush=True)
                continue
    conn.close()
    print(f"完成:{done}/{total} 補上縮圖", flush=True)


if __name__ == "__main__":
    main()
