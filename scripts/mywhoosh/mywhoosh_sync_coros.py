import os
import sys

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]
config_path = CURRENT_DIR.rsplit('/', 1)[0]
sys.path.append(config_path)

from config import DB_DIR, GARMIN_FIT_DIR
from garmin.garmin_db import GarminDB
from coros.coros_client import CorosClient
from coros.sts_config import STS_CONFIG
from oss.aws_oss_client import AwsOssClient
from oss.ali_oss_client import AliOssClient
from mywhoosh.mywhoosh_client import MyWhooshClient
from utils.md5_utils import calculate_md5_file

SYNC_CONFIG = {
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
    "MYWHOOSH_EMAIL": '',
    "MYWHOOSH_PASSWORD": '',
    "SYNC_DAYS": '3',
}


def init():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
    if not os.path.exists(GARMIN_FIT_DIR):
        os.makedirs(GARMIN_FIT_DIR, exist_ok=True)


if __name__ == "__main__":
    # Load environment variables
    for k in SYNC_CONFIG:
        if os.getenv(k):
            SYNC_CONFIG[k] = os.getenv(k)

    # Validate required config
    if not SYNC_CONFIG["MYWHOOSH_EMAIL"] or not SYNC_CONFIG["MYWHOOSH_PASSWORD"]:
        print("ERROR: MYWHOOSH_EMAIL and MYWHOOSH_PASSWORD are required")
        print("Set them as GitHub secrets or environment variables")
        exit(1)

    if not SYNC_CONFIG["COROS_EMAIL"] or not SYNC_CONFIG["COROS_PASSWORD"]:
        print("ERROR: COROS_EMAIL and COROS_PASSWORD are required")
        exit(1)

    init()

    # Initialize DB (reuse garmin_db for tracking synced activities, same as
    # intervals_sync_coros.py — the "mw_" key prefix keeps MyWhoosh activity
    # ids from colliding with Garmin or intervals.icu ids in the same table)
    db_name = "garmin.db"
    garmin_db = GarminDB(db_name)
    if not os.path.exists(os.path.join(DB_DIR, garmin_db.garmin_db_name)):
        garmin_db.initDB()

    # Login to MyWhoosh
    mywhooshClient = MyWhooshClient(SYNC_CONFIG["MYWHOOSH_EMAIL"], SYNC_CONFIG["MYWHOOSH_PASSWORD"])
    mywhooshClient.login()

    # Login to COROS
    corosClient = CorosClient(SYNC_CONFIG["COROS_EMAIL"], SYNC_CONFIG["COROS_PASSWORD"])
    corosClient.login()

    # Fetch recent activities from MyWhoosh
    sync_days = int(SYNC_CONFIG.get("SYNC_DAYS", 3))
    print(f"\nFetching activities from MyWhoosh (last {sync_days} days)...")
    activities = mywhooshClient.getRecentActivities(days=sync_days)

    if not activities:
        print("No activities found")
        exit()

    print(f"Found {len(activities)} activities")

    # Keep a lookup by id so we can re-fetch the full activity dict (we need
    # activityFileId later, not just the id) when we get to the download step
    activities_by_id = {}

    # Check which ones need syncing
    for activity in activities:
        act_id = activity.get("id", "")
        act_title = activity.get("title", "N/A")
        act_start = activity.get("startDatetime", "")

        print(f"  {act_id}: title={act_title}, start={act_start}")

        activities_by_id[str(act_id)] = activity

        # Use the MyWhoosh activity id as our tracking key.
        # We prefix with "mw_" to avoid collisions with garmin/intervals.icu activity ids.
        sync_key = f"mw_{act_id}"
        garmin_db.saveActivity(sync_key)

    un_sync_id_list = garmin_db.getUnSyncActivity() or []
    # Filter to only MyWhoosh activities
    un_sync_id_list = [sid for sid in un_sync_id_list if str(sid).startswith("mw_")]

    if not un_sync_id_list:
        print("No new activities to sync")
        exit()

    print(f"\nActivities to sync: {len(un_sync_id_list)}")

    # Get the STS config for the user's region
    region_id = corosClient.regionId
    sts_cfg = STS_CONFIG.get(region_id, STS_CONFIG.get(1))
    print(f"COROS Region: {region_id}, Bucket: {sts_cfg['bucket']}, Service: {sts_cfg['service']}")

    print(f"\n{'='*50}")
    print(f"Syncing activities: MyWhoosh -> COROS")
    print(f"{'='*50}")

    for sync_key in un_sync_id_list:
        # Extract the MyWhoosh activity id
        mw_id = sync_key.replace("mw_", "")
        activity = activities_by_id.get(mw_id)

        try:
            print(f"\n--- Activity {mw_id} ---")

            if activity is None:
                # Shouldn't normally happen (only possible if the DB has a
                # stale unsynced row from an activity outside the current
                # lookback window) — skip rather than guess at its data.
                print(f"  Activity not in current fetch window, skipping")
                garmin_db.updateExceptionSyncStatus(sync_key)
                continue

            # Step 1: Download FIT file from MyWhoosh (saved as ZIP)
            zip_path = mywhooshClient.downloadFitFile(activity, GARMIN_FIT_DIR)
            if not zip_path or not os.path.exists(zip_path):
                print(f"  Failed to download")
                garmin_db.updateExceptionSyncStatus(sync_key)
                continue

            # Step 2: Upload to cloud storage (S3/OSS)
            if region_id == 2:
                client = AliOssClient(bucket=sts_cfg['bucket'], service=sts_cfg['service'])
            else:
                client = AwsOssClient(bucket=sts_cfg['bucket'], service=sts_cfg['service'])

            file_md5 = calculate_md5_file(zip_path)
            oss_key = f"{corosClient.userId}/{file_md5}.zip"
            print(f"  Uploading to S3: fit_zip/{oss_key}")
            client.multipart_upload(zip_path, oss_key)

            # Step 3: Tell COROS about the file
            size = os.path.getsize(zip_path)
            full_oss_path = f"fit_zip/{corosClient.userId}/{file_md5}.zip"

            upload_result = corosClient.uploadActivity(
                full_oss_path,
                file_md5,
                f"{mw_id}.zip",
                size
            )

            if upload_result:
                garmin_db.updateSyncStatus(sync_key)
                print(f"  SYNC COMPLETE!")
            else:
                print(f"  COROS rejected the upload")
                garmin_db.updateExceptionSyncStatus(sync_key)

        except Exception as err:
            print(f"  Error: {err}")
            garmin_db.updateExceptionSyncStatus(sync_key)

    print(f"\n{'='*50}")
    print("SYNC COMPLETE")
    print(f"{'='*50}")
