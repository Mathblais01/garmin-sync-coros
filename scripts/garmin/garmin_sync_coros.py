import os
import sys

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]
config_path = CURRENT_DIR.rsplit('/', 1)[0]
sys.path.append(config_path)

from config import DB_DIR, GARMIN_FIT_DIR
from garmin.garmin_client import GarminClient
from garmin.garmin_db import GarminDB
from coros.coros_client import CorosClient
from oss.ali_oss_client import AliOssClient
from oss.aws_oss_client import AwsOssClient
from coros.sts_config import STS_CONFIG
from utils.md5_utils import calculate_md5_file

SYNC_CONFIG = {
    'GARMIN_AUTH_DOMAIN': '',
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_NEWEST_NUM': 10000,
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
}


def init(coros_db):
    print(os.path.join(DB_DIR, coros_db.garmin_db_name))
    if not os.path.exists(os.path.join(DB_DIR, coros_db.garmin_db_name)):
        coros_db.initDB()
    if not os.path.exists(GARMIN_FIT_DIR):
        os.mkdir(GARMIN_FIT_DIR)


if __name__ == "__main__":
    # Load environment variables
    for k in SYNC_CONFIG:
        if os.getenv(k):
            SYNC_CONFIG[k] = os.getenv(k)

    db_name = "garmin.db"
    garmin_db = GarminDB(db_name)
    init(garmin_db)

    GARMIN_EMAIL = SYNC_CONFIG["GARMIN_EMAIL"]
    GARMIN_PASSWORD = SYNC_CONFIG["GARMIN_PASSWORD"]
    GARMIN_AUTH_DOMAIN = SYNC_CONFIG["GARMIN_AUTH_DOMAIN"]
    GARMIN_NEWEST_NUM = SYNC_CONFIG["GARMIN_NEWEST_NUM"]

    garminClient = GarminClient(GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_AUTH_DOMAIN, GARMIN_NEWEST_NUM)

    COROS_EMAIL = SYNC_CONFIG["COROS_EMAIL"]
    COROS_PASSWORD = SYNC_CONFIG["COROS_PASSWORD"]
    corosClient = CorosClient(COROS_EMAIL, COROS_PASSWORD)
    corosClient.login()

    print(f"\nFetching activities (limit: {GARMIN_NEWEST_NUM})...")
    all_activities = garminClient.getAllActivities()

    if all_activities is None or len(all_activities) == 0:
        print("No activities found")
        exit()

    print(f"Found {len(all_activities)} activities")

    # --- ACTIVITY TYPE FILTER ---
    # To sync ALL activity types, use this line:
    # ALLOWED_TYPES = None
    # To sync ONLY cycling, comment the line above and uncomment this one:
    ALLOWED_TYPES = ["cycling", "indoor_cycling", "virtual_ride", "gravel_cycling", "mountain_biking", "road_biking", "e_bike_mountain", "e_bike_fitness"]
    
    for activity in all_activities:
        activity_id = activity["activityId"]
        activity_type = activity.get("activityType", {}).get("typeKey", "unknown")
        activity_name = activity.get("activityName", "N/A")
        print(f"Activity {activity_id}: type={activity_type}, name={activity_name}")
    
        if ALLOWED_TYPES is None or activity_type in ALLOWED_TYPES:
            garmin_db.saveActivity(activity_id)
        else:
            print(f"  Skipping ({activity_type} not in allowed types)")

    un_sync_id_list = garmin_db.getUnSyncActivity()
    if un_sync_id_list is None or len(un_sync_id_list) == 0:
        print("No activities to sync")
        exit()

    print(f"Activities to sync: {len(un_sync_id_list)}")
    file_path_list = []

    # Step 1: Download activities from Garmin as ZIP files
    for un_sync_id in un_sync_id_list:
        try:
            print(f"\n--- Downloading activity {un_sync_id} ---")
            file = garminClient.downloadFitActivity(un_sync_id)
            # Save as ZIP (this is how Garmin delivers it)
            file_path = os.path.join(GARMIN_FIT_DIR, f"{un_sync_id}.zip")
            with open(file_path, "wb") as fb:
                fb.write(file)
            print(f"Activity {un_sync_id}: Downloaded {len(file)} bytes -> {file_path}")
            file_path_list.append({
                "un_sync_id": un_sync_id,
                "file_path": file_path
            })
        except Exception as err:
            print(f"Activity {un_sync_id}: Download error - {err}")
            garmin_db.updateExceptionSyncStatus(un_sync_id)

    # Step 2: Upload to cloud storage (S3/OSS) then register with COROS
    print(f"\n{'='*50}")
    print(f"Uploading {len(file_path_list)} activities to COROS via S3/OSS")
    print(f"{'='*50}")

    # Get the STS config for the user's region
    region_id = corosClient.regionId
    sts_cfg = STS_CONFIG.get(region_id, STS_CONFIG.get(1))
    print(f"Region: {region_id}, Bucket: {sts_cfg['bucket']}, Service: {sts_cfg['service']}")

    for un_sync_info in file_path_list:
        try:
            file_path = un_sync_info["file_path"]
            un_sync_id = un_sync_info["un_sync_id"]

            print(f"\n--- Uploading activity {un_sync_id} ---")

            # Initialize the appropriate cloud storage client
            if region_id == 2:
                # China region -> Alibaba Cloud OSS
                client = AliOssClient(
                    bucket=sts_cfg['bucket'],
                    service=sts_cfg['service']
                )
            else:
                # International (region 1 or 3) -> AWS S3
                client = AwsOssClient(
                    bucket=sts_cfg['bucket'],
                    service=sts_cfg['service']
                )

            # Upload ZIP file to cloud storage
            file_md5 = calculate_md5_file(file_path)
            oss_key = f"{corosClient.userId}/{file_md5}.zip"
            print(f"Uploading to cloud storage: fit_zip/{oss_key}")
            client.multipart_upload(file_path, oss_key)

            # Tell COROS about the uploaded file
            size = os.path.getsize(file_path)
            full_oss_path = f"fit_zip/{corosClient.userId}/{file_md5}.zip"
            print(f"Registering with COROS: {full_oss_path} ({size} bytes)")

            upload_result = corosClient.uploadActivity(
                full_oss_path,
                file_md5,
                f"{un_sync_id}.zip",
                size
            )

            if upload_result:
                garmin_db.updateSyncStatus(un_sync_id)
                print(f"Activity {un_sync_id}: SYNC COMPLETE!")
            else:
                print(f"Activity {un_sync_id}: COROS rejected the upload")
                garmin_db.updateExceptionSyncStatus(un_sync_id)

        except Exception as err:
            print(f"Activity {un_sync_id}: Error - {err}")
            garmin_db.updateExceptionSyncStatus(un_sync_id)

    print(f"\n{'='*50}")
    print("SYNC COMPLETE")
    print(f"{'='*50}")
