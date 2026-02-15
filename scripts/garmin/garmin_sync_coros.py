import os
import sys
import zipfile
CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]  # 当前目录
config_path = CURRENT_DIR.rsplit('/', 1)[0]  # 上三级目录
sys.path.append(config_path)
from config import DB_DIR, GARMIN_FIT_DIR
from garmin.garmin_client import GarminClient
from garmin.garmin_db import GarminDB
from coros.coros_client import CorosClient
from oss.ali_oss_client import AliOssClient
from oss.aws_oss_client import AwsOssClient
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
    ## 判断RQ数据库是否存在
    print(os.path.join(DB_DIR, coros_db.garmin_db_name))
    if not os.path.exists(os.path.join(DB_DIR, coros_db.garmin_db_name)):
        ## 初始化建表
        coros_db.initDB()
    if not os.path.exists(GARMIN_FIT_DIR):
        os.mkdir(GARMIN_FIT_DIR)

def extract_fit_from_zip(zip_path):
    """
    Extract the .fit file from a zip archive.
    Returns the path to the extracted .fit file, or None if extraction fails.
    """
    try:
        extract_dir = os.path.dirname(zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Find .fit files in the archive
            fit_files = [f for f in zip_ref.namelist() if f.lower().endswith('.fit')]
            if fit_files:
                # Extract the first .fit file found
                fit_filename = fit_files[0]
                zip_ref.extract(fit_filename, extract_dir)
                extracted_path = os.path.join(extract_dir, fit_filename)
                
                # Rename to match the activity ID for consistency
                base_name = os.path.basename(zip_path).replace('.zip', '.fit')
                final_path = os.path.join(extract_dir, base_name)
                
                # If the extracted file has a different name, rename it
                if extracted_path != final_path:
                    if os.path.exists(final_path):
                        os.remove(final_path)
                    os.rename(extracted_path, final_path)
                
                print(f"Extracted FIT file: {final_path}")
                return final_path
            else:
                print(f"No .fit file found in {zip_path}")
                return None
    except Exception as e:
        print(f"Error extracting FIT file from {zip_path}: {e}")
        return None

def save_and_extract_fit(file_data, activity_id, garmin_fit_dir):
    """
    Save downloaded data and extract FIT file.
    Handles both ZIP files and raw FIT files.
    Returns the path to the FIT file, or None if failed.
    """
    if not file_data or len(file_data) == 0:
        print(f"Activity {activity_id}: No data received from Garmin")
        return None
    
    print(f"Activity {activity_id}: Downloaded {len(file_data)} bytes")
    
    # Check what type of data we received
    if len(file_data) < 10:
        print(f"Activity {activity_id}: Data too small, likely an error")
        print(f"Activity {activity_id}: Content preview: {file_data[:100]}")
        return None
    
    # Check if it's a ZIP file (starts with PK)
    if file_data[:2] == b'PK':
        print(f"Activity {activity_id}: Received ZIP file")
        zip_path = os.path.join(garmin_fit_dir, f"{activity_id}.zip")
        with open(zip_path, "wb") as fb:
            fb.write(file_data)
        
        fit_path = extract_fit_from_zip(zip_path)
        
        # Clean up zip file
        try:
            os.remove(zip_path)
        except:
            pass
        
        return fit_path
    
    # Check if it's a FIT file (FIT files start with header size byte, usually 12 or 14)
    # and contain ".FIT" signature at bytes 8-11
    elif len(file_data) > 12 and file_data[8:12] == b'.FIT':
        print(f"Activity {activity_id}: Received raw FIT file")
        fit_path = os.path.join(garmin_fit_dir, f"{activity_id}.fit")
        with open(fit_path, "wb") as fb:
            fb.write(file_data)
        return fit_path
    
    # Check if it's an HTML error page
    elif file_data[:5] == b'<!DOC' or file_data[:5] == b'<html' or file_data[:1] == b'<':
        print(f"Activity {activity_id}: Received HTML error page instead of file")
        print(f"Activity {activity_id}: Content preview: {file_data[:200].decode('utf-8', errors='ignore')}")
        return None
    
    # Unknown format - save as zip and try to extract anyway
    else:
        print(f"Activity {activity_id}: Unknown format, first bytes: {file_data[:20]}")
        # Try saving as FIT directly (some FIT files have non-standard headers)
        fit_path = os.path.join(garmin_fit_dir, f"{activity_id}.fit")
        with open(fit_path, "wb") as fb:
            fb.write(file_data)
        print(f"Activity {activity_id}: Saved as FIT file: {fit_path}")
        return fit_path


if __name__ == "__main__":
    # 首先读取 面板变量 或者 github action 运行变量
    for k in SYNC_CONFIG:
        if os.getenv(k):
            v = os.getenv(k)
            SYNC_CONFIG[k] = v
    
    ## db 名称
    db_name = "garmin.db"
    ## 建立DB链接
    garmin_db = GarminDB(db_name)
    ## 初始化DB位置和下载文件位置
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
    
    print(f"Fetching activities (limit: {GARMIN_NEWEST_NUM})...")
    all_activities = garminClient.getAllActivities()
    
    if all_activities == None or len(all_activities) == 0:
        print("No activities found")
        exit()
    
    print(f"Found {len(all_activities)} activities")
    
    for activity in all_activities:
        activity_id = activity["activityId"]
        garmin_db.saveActivity(activity_id)
    
    un_sync_id_list = garmin_db.getUnSyncActivity()
    if un_sync_id_list == None or len(un_sync_id_list) == 0:
        print("No activities to sync")
        exit()
    
    print(f"Activities to sync: {len(un_sync_id_list)}")
    file_path_list = []
    
    for un_sync_id in un_sync_id_list:
        try:
            print(f"\nDownloading activity {un_sync_id}...")
            # Download the file from Garmin
            file_data = garminClient.downloadFitActivity(un_sync_id)
            
            # Save and extract FIT file
            fit_path = save_and_extract_fit(file_data, un_sync_id, GARMIN_FIT_DIR)
            
            if fit_path and os.path.exists(fit_path):
                un_sync_info = {
                    "un_sync_id": un_sync_id,
                    "file_path": fit_path
                }
                file_path_list.append(un_sync_info)
                print(f"Activity {un_sync_id}: Ready for upload ({os.path.getsize(fit_path)} bytes)")
            else:
                print(f"Activity {un_sync_id}: Failed to process")
                garmin_db.updateExceptionSyncStatus(un_sync_id)
            
        except Exception as err:
            print(f"Activity {un_sync_id}: Error - {err}")
            garmin_db.updateExceptionSyncStatus(un_sync_id)

    print(f"\nUploading {len(file_path_list)} activities to COROS...")
    
    for un_sync_info in file_path_list:
        try:
            client = None
            ## 中国区使用阿里云OSS
            if corosClient.regionId == 2:
                client = AliOssClient()
            elif corosClient.regionId == 1 or corosClient.regionId == 3:
                client = AwsOssClient()
            
            file_path = un_sync_info["file_path"]
            un_sync_id = un_sync_info["un_sync_id"]
            
            print(f"\nUploading activity {un_sync_id}...")
            
            # Upload the .fit file
            file_md5 = calculate_md5_file(file_path)
            oss_obj = client.multipart_upload(file_path, f"{corosClient.userId}/{file_md5}.fit")
            
            print(f"Activity {un_sync_id}: Uploaded to cloud storage")
            
            size = os.path.getsize(file_path)
            
            # Tell COROS about the .fit file
            upload_result = corosClient.uploadActivity(
                f"fit_zip/{corosClient.userId}/{file_md5}.fit",
                file_md5,
                f"{un_sync_id}.fit",
                size
            )
            print(f"Activity {un_sync_id}: COROS response: {upload_result}")
            
            if upload_result:
                garmin_db.updateSyncStatus(un_sync_id)
                print(f"Activity {un_sync_id}: Sync complete!")
                
            # Clean up the fit file after upload
            try:
                os.remove(file_path)
            except:
                pass
                
        except Exception as err:
            print(f"Activity {un_sync_id}: Upload error - {err}")
            garmin_db.updateExceptionSyncStatus(un_sync_id)

    print("\nSync process complete!")
