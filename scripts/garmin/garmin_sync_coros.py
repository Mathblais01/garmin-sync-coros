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
    all_activities = garminClient.getAllActivities()
    if all_activities == None or len(all_activities) == 0:
        exit()
    for activity in all_activities:
        activity_id = activity["activityId"]
        garmin_db.saveActivity(activity_id)
    
    un_sync_id_list = garmin_db.getUnSyncActivity()
    if un_sync_id_list == None or len(un_sync_id_list) == 0:
        exit()
    file_path_list = []
    
    for un_sync_id in un_sync_id_list:
        try:
            # Download the zip file from Garmin
            file = garminClient.downloadFitActivity(un_sync_id)
            zip_path = os.path.join(GARMIN_FIT_DIR, f"{un_sync_id}.zip")
            with open(zip_path, "wb") as fb:
                fb.write(file)
            
            # Extract the .fit file from the zip
            fit_path = extract_fit_from_zip(zip_path)
            
            if fit_path and os.path.exists(fit_path):
                un_sync_info = {
                    "un_sync_id": un_sync_id,
                    "file_path": fit_path  # Use the extracted .fit file, not the .zip
                }
                file_path_list.append(un_sync_info)
                
                # Clean up the zip file
                try:
                    os.remove(zip_path)
                except:
                    pass
            else:
                print(f"Failed to extract FIT file for activity {un_sync_id}")
                garmin_db.updateExceptionSyncStatus(un_sync_id)
            
        except Exception as err:
            print(err)

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
            
            # Upload the .fit file (not .zip)
            file_md5 = calculate_md5_file(file_path)
            oss_obj = client.multipart_upload(file_path, f"{corosClient.userId}/{file_md5}.fit")
            
            print(f"File {corosClient.userId}/{file_md5}.fit uploaded successfully!")
            
            size = os.path.getsize(file_path)
            
            # Tell COROS about the .fit file (not .zip)
            upload_result = corosClient.uploadActivity(
                f"fit_zip/{corosClient.userId}/{file_md5}.fit",
                file_md5,
                f"{un_sync_id}.fit",
                size
            )
            print(upload_result)
            
            if upload_result:
                garmin_db.updateSyncStatus(un_sync_id)
                
            # Clean up the fit file after upload
            try:
                os.remove(file_path)
            except:
                pass
                
        except Exception as err:
            print(err)
            garmin_db.updateExceptionSyncStatus(un_sync_id)
            exit()
