import logging
import os
from enum import Enum, auto
import requests

import garth


from .garmin_url_dict import GARMIN_URL_DICT

logger = logging.getLogger(__name__)

# Token storage directory (persisted via git commit in workflow)
TOKEN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.garth')


class GarminClient:
  def __init__(self, email, password, auth_domain, newest_num):
        self.auth_domain = auth_domain
        self.email = email
        self.password = password
        self.garthClient = garth
        self.newestNum = int(newest_num)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.88 Safari/537.36",
            "origin": GARMIN_URL_DICT.get("SSO_URL_ORIGIN"),
            "nk": "NT"
        }

        # Configure domain if China region
        if self.auth_domain and str(self.auth_domain).upper() == "CN":
            self.garthClient.configure(domain="garmin.cn")

        # Try to resume saved session first
        self._try_resume_token()

  def _try_resume_token(self):
        """Try to resume a previously saved garth token."""
        try:
            if os.path.exists(TOKEN_DIR):
                self.garthClient.resume(TOKEN_DIR)
                # Verify the token still works
                garth.client.username
                print("Garmin: Resumed saved session successfully")
                return True
        except Exception as e:
            print(f"Garmin: Saved session expired or invalid ({e})")
        return False

  def _save_token(self):
        """Save the current garth token for future use."""
        try:
            os.makedirs(TOKEN_DIR, exist_ok=True)
            self.garthClient.save(TOKEN_DIR)
            print(f"Garmin: Session token saved to {TOKEN_DIR}")
        except Exception as e:
            print(f"Garmin: Failed to save token ({e})")

  ## Login decorator
  def login(func):    
    def ware(self, *args, **kwargs):    
      try:
         garth.client.username
      except Exception:
        logger.warning("Garmin is not logging in or the token has expired.")

        # Try resuming saved token first
        if self._try_resume_token():
            return func(self, *args, **kwargs)

        # Fresh login required
        print("Garmin: Performing fresh login...")
        if self.auth_domain and str(self.auth_domain).upper() == "CN":
          self.garthClient.configure(domain="garmin.cn")
        self.garthClient.login(self.email, self.password)
        
        del self.garthClient.client.sess.headers['User-Agent']

        # Save token for future runs
        self._save_token()

      return func(self, *args, **kwargs)
    return ware
  
  @login 
  def download(self, path, **kwargs):
     return self.garthClient.download(path, **kwargs)
  
  @login 
  def connectapi(self, path, **kwargs):
      return self.garthClient.connectapi(path, **kwargs)
     

  ## Get activities
  def getActivities(self, start:int, limit:int):
     
     params = {"start": str(start), "limit": str(limit)}
     activities =  self.connectapi(path=GARMIN_URL_DICT["garmin_connect_activities"], params=params)
     return activities;

  ## Get all activities (respects newestNum limit)
  def getAllActivities(self): 
    all_activities = []
    start = 0
    limit = 100
    if 0 < self.newestNum < 100:
      limit = self.newestNum
    
    while(True):
      activities = self.getActivities(start=start, limit=limit)
      if len(activities) > 0:
        all_activities.extend(activities)
        if self.newestNum > 0 and len(all_activities) >= self.newestNum:
          return all_activities[:self.newestNum]
      else:
        return all_activities
      start += limit

  ## Download FIT activity
  def downloadFitActivity(self, activity):
    download_fit_activity_url_prefix = GARMIN_URL_DICT["garmin_connect_fit_download"]
    download_fit_activity_url = f"{download_fit_activity_url_prefix}/{activity}"
    response = self.download(download_fit_activity_url)
    return response

  @login  
  def upload_activity(self, activity_path: str):
    """Upload activity in fit format from file."""
    file_base_name = os.path.basename(activity_path)
    file_extension = file_base_name.split(".")[-1]
    allowed_file_extension = (
        file_extension.upper() in ActivityUploadFormat.__members__
    )

    if allowed_file_extension:
       try:
        with open(activity_path, 'rb') as file:
          file_data = file.read()
          fields = {
              'file': (file_base_name, file_data, 'text/plain')
          }

          url_path = GARMIN_URL_DICT["garmin_connect_upload"]
          upload_url = f"https://connectapi.{self.garthClient.client.domain}{url_path}"
          self.headers['Authorization'] = str(self.garthClient.client.oauth2_token)
          response = requests.post(upload_url, headers=self.headers, files=fields)
          res_code = response.status_code
          result = response.json()
          uploadId =  result.get("detailedImportResult").get('uploadId')
          isDuplicateUpload = uploadId == None or uploadId == ''
          if res_code == 202 and not isDuplicateUpload:
              status = "SUCCESS"
          elif res_code == 409 and result.get("detailedImportResult").get("failures")[0].get('messages')[0].get('content') == "Duplicate Activity.":
              status = "DUPLICATE_ACTIVITY" 
       except Exception as e:
            print(e)
            status = "UPLOAD_EXCEPTION"
       finally:
            return status
    else:
        return "UPLOAD_EXCEPTION"
  

class ActivityUploadFormat(Enum):
  FIT = auto()
  GPX = auto()
  TCX = auto()

class GarminNoLoginException(Exception):
    """Raised when rate limit is exceeded."""

    def __init__(self, status):
        """Initialize."""
        super(GarminNoLoginException, self).__init__(status)
        self.status = status
