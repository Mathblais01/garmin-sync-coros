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
from intervals.intervals_client import IntervalsClient
from utils.md5_utils import calculate_md5_file

SYNC_CONFIG = {
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
    "INTERVALS_ATHLETE_ID": '',
    "INTERVALS_API_KEY": '',
    "SYNC_DAYS": '3',
}

# --- ACTIVITY TYPE FILTER ---
# To sync ALL activity types, use this line:
# ALLOWED_TYPES = None
# To sync ONLY cycling, uncomment this line:
ALLOWED_TYPES = ["Ride", "VirtualRide"]


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
    if not SYNC_CONFIG["INTERVALS_ATHLETE_ID"] or not SYNC_CONFIG["INTERVALS_API_KEY"]:
        print("ERROR: INTERVALS_ATHLETE_ID and INTERVALS_API_KEY are required")
        print("Set them as GitHub secrets or environment variables")
        exit(1)

    if not SYNC_CONFIG["COROS_EMAIL"] or not SYNC_CONFIG["COROS_PASSWORD"]:
        print("ERROR: COROS_EMAIL and COROS_PASSWORD are required")
        exit(1)

    init()

    # Initialize DB (reuse garmin_db for tracking synced activities)
    db_name = "garmin.db"
    garmin_db = GarminDB(db_name)
    if not os.path.exists(os.path.join(DB_DIR, garmin_db.garmin_db_name)):
        garmin_db.initDB()

    # Login to COROS
    corosClient = CorosClient(SYNC_CONFIG["COROS_EMAIL"], SYNC_CONFIG["COROS_PASSWORD"])
    corosClient.login()

    # Initialize intervals.icu client
    intervalsClient = IntervalsClient(
        SYNC_CONFIG["INTERVALS_ATHLETE_ID"],
        SYNC_CONFIG["INTERVALS_API_KEY"]
    )

    # Fetch recent activities from intervals.icu
    sync_days = int(SYNC_CONFIG.get("SYNC_DAYS", 3))
    print(f"\nFetching activities from intervals.icu (last {sync_days} days)...")
    activities = intervalsClient.getRecentActivities(days=sync_days, types=ALLOWED_TYPES)

    if not activities:
        print("No activities found")
        exit()

    print(f"Found {len(activities)} activities")

    # Check which ones need syncing
    to_sync = []
    for activity in activities:
        act_id = activity.get("id", "")
        act_type = activity.get("type", "unknown")
        act_name = activity.get("name", "N/A")
        act_date = activity.get("start_date_local", "")[:10]
        
        print(f"  {act_id}: type={act_type}, date={act_date}, name={act_name}")

        # Use the intervals.icu ID as our tracking key
        # We prefix with "icu_" to avoid collisions with garmin activity IDs
        sync_key = f"icu_{act_id}"
        garmin_db.saveActivity(sync_key)

    un_sync_id_list = garmin_db.getUnSyncActivity()
    # Filter to only intervals.icu activities
    un_sync_id_list = [sid for sid in un_sync_id_list if str(sid).startswith("icu_")]

    if not un_sync_id_list:
        print("No new activities to sync")
        exit()

    print(f"\nActivities to sync: {len(un_sync_id_list)}")

    # Download and upload each activity
    # Get the STS config for the user's region
    region_id = corosClient.regionId
    sts_cfg = STS_CONFIG.get(region_id, STS_CONFIG.get(1))
    print(f"COROS Region: {region_id}, Bucket: {sts_cfg['bucket']}, Service: {sts_cfg['service']}")

    print(f"\n{'='*50}")
    print(f"Syncing activities: intervals.icu -> COROS")
    print(f"{'='*50}")

    for sync_key in un_sync_id_list:
        # Extract the intervals.icu activity ID
        icu_id = sync_key.replace("icu_", "")

        try:
            print(f"\n--- Activity {icu_id} ---")

            # Step 1: Download FIT file from intervals.icu (saved as ZIP)
            zip_path = intervalsClient.downloadFitFile(icu_id, GARMIN_FIT_DIR)
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
                f"{icu_id}.zip",
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
