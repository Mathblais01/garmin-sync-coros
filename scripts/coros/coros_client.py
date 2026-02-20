import urllib3
import json
import hashlib
import os

import certifi

from coros.region_config import REGIONCONFIG
from coros.sts_config import STS_CONFIG


class CorosClient:

    def __init__(self, email, password) -> None:
        self.email = email
        self.password = password
        self.req = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
        self.accessToken = None
        self.userId = None
        self.regionId = None
        self.teamapi = None
        self.trainingHub = None

    def login(self):
        """Login to COROS and get access token."""
        login_url = "https://teamcnapi.coros.com/account/login"

        login_data = {
            "account": self.email,
            "pwd": hashlib.md5(self.password.encode()).hexdigest(),
            "accountType": 2,
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        login_body = json.dumps(login_data)
        response = self.req.request('POST', login_url, body=login_body, headers=headers)

        login_response = json.loads(response.data)
        login_result = login_response["result"]
        if login_result != "0000":
            raise CorosLoginError("COROS login failed: " + login_response.get("message", "Unknown error"))

        self.accessToken = login_response["data"]["accessToken"]
        self.userId = login_response["data"]["userId"]
        self.regionId = login_response["data"]["regionId"]
        self.teamapi = REGIONCONFIG[self.regionId]['teamapi']
        self.trainingHub = REGIONCONFIG[self.regionId]['hostname']
        print(f"COROS login successful - Region: {self.regionId}, User: {self.userId}")
        print(f"  Team API: {self.teamapi}")
        print(f"  Training Hub: {self.trainingHub}")

    def uploadActivity(self, oss_object, md5, fileName, size):
        """
        Register an uploaded file with COROS.
        The file must already be uploaded to S3/OSS before calling this.
        
        Args:
            oss_object: The S3/OSS object path (e.g., "fit_zip/userId/md5.zip")
            md5: MD5 hash of the file
            fileName: Original filename (e.g., "activityId.zip")
            size: File size in bytes
        """
        if self.accessToken is None:
            self.login()

        # Get bucket and service name from STS config for this region
        sts_cfg = STS_CONFIG.get(self.regionId, STS_CONFIG.get(1))
        bucket = sts_cfg['bucket']
        serviceName = sts_cfg['service']

        upload_url = f"{self.teamapi}/activity/fit/import"

        headers = {
            "Accept": "application/json, text/plain, */*",
            "accesstoken": self.accessToken,
        }

        try:
            data = {
                "source": 1,
                "timezone": -136,
                "bucket": bucket,
                "md5": md5,
                "size": size,
                "object": oss_object,
                "serviceName": serviceName,
                "oriFileName": fileName
            }
            json_str = json.dumps(data)
            print(f"COROS import request: {json_str}")

            response = self.req.request(
                method='POST',
                url=upload_url,
                fields={"jsonParameter": json_str},
                headers=headers
            )
            upload_response = json.loads(response.data)
            print(f"COROS import response: {upload_response}")

            if upload_response.get("result") != "0000":
                print(f"COROS import failed: {upload_response.get('message', 'Unknown')}")
                return False

            status = upload_response.get("data", {}).get("status")
            # status 2 = success, -2 = duplicate (already exists)
            if status == 2 or status == -2:
                return True
            return status is not None and status > 0

        except Exception as err:
            print(f"COROS import exception: {err}")
            return False

    def getActivities(self, size: int, page: int):
        self.checkToken()
        activitys_url = f"{self.teamapi}/activity/query?size={size}&pageNumber={page}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "accesstoken": self.accessToken,
        }
        try:
            response = self.req.request(method='GET', url=activitys_url, headers=headers)
            return json.loads(response.data)
        except Exception as err:
            print(f"getActivities error: {err}")
            return None

    def getAllActivities(self):
        all_activities = []
        size = 200
        page = 1
        while True:
            activities = self.getActivities(size, page)
            if activities is None:
                return all_activities
            totalPage = activities.get('data', {}).get('totalPage', 0)
            if totalPage >= page:
                all_activities.extend(activities.get('data', {}).get('dataList', []))
            else:
                return all_activities
            page += 1

    def downloadActivitie(self, id, sport_type):
        self.checkToken()
        get_activity_download_url = f"{self.teamapi}/activity/detail/download?labelId={id}&sportType={sport_type}&fileType=4"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "accesstoken": self.accessToken,
        }
        try:
            response = self.req.request(method='POST', url=get_activity_download_url, headers=headers)
            response_json = json.loads(response.data)
            download_url = response_json['data']['fileUrl']
            return self.req.request(method='GET', url=download_url, headers=headers)
        except Exception as err:
            print(f"downloadActivitie error: {err}")
            return None

    def checkToken(self):
        if self.accessToken is None:
            self.login()


class CorosLoginError(Exception):
    def __init__(self, status):
        super(CorosLoginError, self).__init__(status)
        self.status = status


class CorosActivityUploadError(Exception):
    def __init__(self, status):
        super(CorosActivityUploadError, self).__init__(status)
        self.status = status
