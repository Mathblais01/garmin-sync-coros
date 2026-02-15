import os
import sys
import zipfile
import shutil

CURRENT_DIR = os.path.split(os.path.abspath(__file__))[0]
config_path = CURRENT_DIR.rsplit('/', 1)[0]
sys.path.append(config_path)

from config import DB_DIR, GARMIN_FIT_DIR
from garmin.garmin_client import GarminClient
from garmin.garmin_db import GarminDB
from coros.coros_client import CorosClient
from utils.md5_utils import calculate_md5_file

SYNC_CONFIG = {
    'GARMIN_AUTH_DOMAIN': '',
    'GARMIN_EMAIL': '',
    'GARMIN_PASSWORD': '',
    'GARMIN_NEWEST_NUM': 10000,
    "COROS_EMAIL": '',
    "COROS_PASSWORD": '',
}

DEBUG_FIT_DIR = os.path.join(os.path.dirname(os.path.dirname(config_path)), 'debug-fit-files')


def init(coros_db):
    print(os.path.join(DB_DIR, coros_db.garmin_db_name))
    if not os.path.exists(os.path.join(DB_DIR, coros_db.garmin_db_name)):
        coros_db.initDB()
    if not os.path.exists(GARMIN_FIT_DIR):
        os.mkdir(GARMIN_FIT_DIR)
    if not os.path.exists(DEBUG_FIT_DIR):
        os.makedirs(DEBUG_FIT_DIR)
        print(f"Created debug directory: {DEBUG_FIT_DIR}")


def extract_fit_from_zip(zip_path):
    """Extract the .fit file from a zip archive."""
    try:
        extract_dir = os.path.dirname(zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            fit_files = [f for f in zip_ref.namelist() if f.lower().endswith('.fit')]
            if fit_files:
                fit_filename = fit_files[0]
                zip_ref.extract(fit_filename, extract_dir)
                extracted_path = os.path.join(extract_dir, fit_filename)
                
                base_name = os.path.basename(zip_path).replace('.zip', '.fit')
                final_path = os.path.join(extract_dir, base_name)
                
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
    """Save downloaded data and extract FIT file."""
    if not file_data or len(file_data) == 0:
        print(f"Activity {activity_id}: No data received from Garmin")
        return None
    
    print(f"Activity {activity_id}: Downloaded {len(file_data)} bytes")
    
    if len(file_data) < 10:
        print(f"Activity {activity_id}: Data too small")
        return None
    
    # Check if it's a ZIP file (starts with PK)
    if file_data[:2] == b'PK':
        print(f"Activity {activity_id}: Received ZIP file")
        zip_path = os.path.join(garmin_fit_dir, f"{activity_id}.zip")
        with open(zip_path, "wb") as fb:
            fb.write(file_data)
        
        fit_path = extract_fit_from_zip(zip_path)
        
        try:
            os.remove(zip_path)
        except:
            pass
        
        return fit_path
    
    # Check if it's already a FIT file
    elif len(file_data) > 12 and file_data[8:12] == b'.FIT':
        print(f"Activity {activity_id}: Received raw FIT file")
        fit_path = os.path.join(garmin_fit_dir, f"{activity_id}.fit")
        with open(fit_path, "wb") as fb:
            fb.write(file_data)
        return fit_path
    
    # Check if it's an HTML error page
    elif file_data[:5] == b'<!DOC' or file_data[:5] == b'<html' or file_data[:1] == b'<':
        print(f"Activity {activity_id}: Received HTML error page")
        return None
    
    # Unknown format - try saving as FIT
    else:
        print(f"Activity {activity_id}: Unknown format, saving as FIT")
        fit_path = os.path.join(garmin_fit_dir, f"{activity_id}.fit")
        with open(fit_path, "wb") as fb:
            fb.write(file_data)
        return fit_path


def validate_fit_file(fit_path):
    """Basic validation of FIT file structure."""
    try:
        with open(fit_path, 'rb') as f:
            header = f.read(14)
            if len(header) < 12:
                return False
            if header[8:12] == b'.FIT':
                print(f"FIT validation: Valid")
                return True
            else:
                print(f"FIT validation: Invalid signature")
                return False
    except Exception as e:
        print(f"FIT validation error: {e}")
        return False


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
    
    if not all_activities:
        print("No activities found")
        exit()
    
    print(f"Found {len(all_activities)} activities")
    
    for activity in all_activities:
        activity_id = activity["activityId"]
        garmin_db.saveActivity(activity_id)
    
    un_sync_id_list = garmin_db.getUnSyncActivity()
    if not un_sync_id_list:
        print("No activities to sync")
        exit()
    
    print(f"Activities to sync: {len(un_sync_id_list)}")
    file_path_list = []
    
    # Download activities from Garmin
    for un_sync_id in un_sync_id_list:
        try:
            print(f"\n--- Downloading activity {un_sync_id} ---")
            file_data = garminClient.downloadFitActivity(un_sync_id)
            fit_path = save_and_extract_fit(file_data, un_sync_id, GARMIN_FIT_DIR)
            
            if fit_path and os.path.exists(fit_path):
                is_valid = validate_fit_file(fit_path)
                
                # Save debug copy
                debug_copy = os.path.join(DEBUG_FIT_DIR, os.path.basename(fit_path))
                shutil.copy2(fit_path, debug_copy)
                
                file_path_list.append({
                    "un_sync_id": un_sync_id,
                    "file_path": fit_path,
                    "is_valid": is_valid
                })
                print(f"Activity {un_sync_id}: Ready ({os.path.getsize(fit_path)} bytes)")
            else:
                print(f"Activity {un_sync_id}: Failed to download")
                garmin_db.updateExceptionSyncStatus(un_sync_id)
                
        except Exception as err:
            print(f"Activity {un_sync_id}: Error - {err}")
            garmin_db.updateExceptionSyncStatus(un_sync_id)

    # Upload activities to COROS using DIRECT UPLOAD
    print(f"\n{'='*50}")
    print(f"Uploading {len(file_path_list)} activities to COROS (DIRECT UPLOAD)")
    print(f"{'='*50}")
    
    for info in file_path_list:
        un_sync_id = info["un_sync_id"]
        file_path = info["file_path"]
        
        try:
            print(f"\n--- Uploading activity {un_sync_id} ---")
            
            # Use direct upload (bypasses S3)
            success = corosClient.directUploadFit(file_path)
            
            if success:
                garmin_db.updateSyncStatus(un_sync_id)
                print(f"Activity {un_sync_id}: SYNC COMPLETE!")
            else:
                print(f"Activity {un_sync_id}: Upload failed")
                garmin_db.updateExceptionSyncStatus(un_sync_id)
                
        except Exception as err:
            print(f"Activity {un_sync_id}: Error - {err}")
            garmin_db.updateExceptionSyncStatus(un_sync_id)

    # Summary
    print(f"\n{'='*50}")
    print("SYNC SUMMARY")
    print(f"{'='*50}")
    print(f"Debug FIT files saved to: {DEBUG_FIT_DIR}")
    if os.path.exists(DEBUG_FIT_DIR):
        for f in os.listdir(DEBUG_FIT_DIR):
            fpath = os.path.join(DEBUG_FIT_DIR, f)
            print(f"  {f}: {os.path.getsize(fpath)} bytes")
    print("\nSync process complete!")
