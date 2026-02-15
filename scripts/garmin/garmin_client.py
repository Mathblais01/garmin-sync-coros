"""
Fixed Garmin Client - Extracts FIT files from ZIP downloads
============================================================

This is a patched version of garmin_client.py for the XiaoSiHwang/garmin-sync-coros repo.

ISSUE: Garmin's API returns a ZIP file containing the FIT file when downloading 
"original" format. The original code saved the ZIP directly as a .fit file,
resulting in corrupted/invalid FIT files that COROS couldn't process.

FIX: This version extracts the actual FIT file from inside the ZIP.

To use: Replace scripts/garmin/garmin_client.py in your fork with this file.
"""

import io
import zipfile
import logging
from config import config
from garth.exc import GarthHTTPError
from garminconnect import Garmin

# Set up logging
logger = logging.getLogger(__name__)

# Quiet noisy libraries
logging.getLogger("garminconnect").setLevel(logging.WARNING)
logging.getLogger("garth").setLevel(logging.WARNING)


def need_login(func):
    """Decorator to ensure login before API calls."""
    def wrapper(self, *args, **kwargs):
        if not self.client:
            self.initClient()
        try:
            return func(self, *args, **kwargs)
        except GarthHTTPError:
            logger.warning("Session expired, re-authenticating...")
            self.initClient()
            return func(self, *args, **kwargs)
    return wrapper


class GarminClient:
    """
    Client for interacting with Garmin Connect API.
    
    Handles authentication, activity retrieval, and FIT file downloads.
    """
    
    def __init__(self, email, password, auth_domain, is_only_running=False, newestNum=0):
        self.email = email
        self.password = password
        self.auth_domain = auth_domain
        self.is_only_running = is_only_running
        self.newestNum = newestNum
        self.client = None
        
    def initClient(self):
        """Initialize and authenticate the Garmin client."""
        try:
            self.client = Garmin(self.email, self.password)
            if self.auth_domain and self.auth_domain.upper() == "CN":
                self.client = Garmin(self.email, self.password, is_cn=True)
            self.client.login()
            logger.info("Successfully logged into Garmin Connect")
        except Exception as e:
            logger.error(f"Failed to login to Garmin Connect: {e}")
            raise
            
    @need_login
    def getActivities(self, start=0, limit=100):
        """Get a page of activities from Garmin Connect."""
        return self.client.get_activities(start, limit)
    
    def getAllActivities(self):
        """
        Get all activities, respecting the newestNum limit if set.
        
        Returns:
            list: List of activity dictionaries
        """
        all_activities = []
        start = 0
        limit = 100
        
        # If newestNum is set and small, use it as the limit
        if 0 < self.newestNum < 100:
            limit = self.newestNum
            
        while True:
            activities = self.getActivities(start=start, limit=limit)
            
            if len(activities) > 0:
                all_activities.extend(activities)
                
                # Check if we've reached the desired number
                if self.newestNum > 0 and len(all_activities) >= self.newestNum:
                    logger.info(f"Reached newestNum limit: {self.newestNum}")
                    return all_activities[:self.newestNum]
            else:
                # No more activities
                break
                
            start += limit
            
        logger.info(f"Retrieved {len(all_activities)} total activities from Garmin")
        return all_activities
    
    @need_login
    def downloadFitActivity(self, activity_id):
        """
        Download a FIT file for a specific activity.
        
        IMPORTANT: Garmin's API returns a ZIP file containing the FIT file
        when downloading in "original" format. This method extracts the 
        actual FIT file from the ZIP.
        
        Args:
            activity_id: The Garmin activity ID
            
        Returns:
            bytes: The raw FIT file data (extracted from ZIP)
            
        Raises:
            Exception: If download fails or ZIP doesn't contain a FIT file
        """
        try:
            # Download the activity in "original" format
            # This returns ZIP data, not raw FIT data!
            zip_data = self.client.download_activity(
                activity_id, 
                dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
            )
            
            # Check if the data is actually a ZIP file
            if zip_data[:2] == b'PK':  # ZIP magic bytes
                logger.debug(f"Activity {activity_id}: Extracting FIT from ZIP")
                fit_data = self._extract_fit_from_zip(zip_data, activity_id)
                logger.info(f"Activity {activity_id}: Extracted FIT file ({len(fit_data)} bytes)")
                return fit_data
            else:
                # Data is already a FIT file (or some other format)
                # FIT files start with header size byte (usually 12 or 14)
                logger.debug(f"Activity {activity_id}: Data is already extracted ({len(zip_data)} bytes)")
                return zip_data
                
        except Exception as e:
            logger.error(f"Failed to download activity {activity_id}: {e}")
            raise
    
    def _extract_fit_from_zip(self, zip_data, activity_id):
        """
        Extract the FIT file from a ZIP archive.
        
        Args:
            zip_data: Raw bytes of the ZIP file
            activity_id: Activity ID (for logging)
            
        Returns:
            bytes: The extracted FIT file data
            
        Raises:
            ValueError: If no FIT file found in the ZIP
        """
        try:
            # Create a file-like object from the bytes
            zip_buffer = io.BytesIO(zip_data)
            
            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                # List all files in the ZIP
                file_list = zf.namelist()
                logger.debug(f"Activity {activity_id}: ZIP contains {file_list}")
                
                # Look for a .fit file
                fit_files = [f for f in file_list if f.lower().endswith('.fit')]
                
                if not fit_files:
                    # Some ZIPs might have the FIT in a subdirectory
                    # or with a different naming pattern
                    logger.warning(f"Activity {activity_id}: No .fit file found in ZIP. Files: {file_list}")
                    
                    # Try to find any file that might be a FIT
                    # (sometimes they have activity ID as filename without extension)
                    for filename in file_list:
                        if str(activity_id) in filename or not '.' in filename:
                            try:
                                data = zf.read(filename)
                                # Check if it looks like FIT data
                                # FIT files have a specific header structure
                                if len(data) > 12:
                                    logger.info(f"Activity {activity_id}: Trying file '{filename}'")
                                    return data
                            except Exception:
                                continue
                    
                    raise ValueError(f"No FIT file found in ZIP for activity {activity_id}")
                
                # Read the first (usually only) FIT file
                fit_filename = fit_files[0]
                fit_data = zf.read(fit_filename)
                
                logger.debug(f"Activity {activity_id}: Extracted '{fit_filename}' ({len(fit_data)} bytes)")
                return fit_data
                
        except zipfile.BadZipFile as e:
            logger.error(f"Activity {activity_id}: Invalid ZIP file: {e}")
            raise ValueError(f"Downloaded data is not a valid ZIP file: {e}")
