import urllib3
import json
import uuid
import os
import time
import zipfile
import certifi


class MyWhooshClient:
    """
    Client for the unofficial MyWhoosh mobile API.

    There is no public/documented MyWhoosh API — these endpoints come from
    community reverse-engineering (see jdelrue/mywoosh2garmin on GitHub) and
    could change or break without notice.
    """

    LOGIN_URL = "https://services.mywhoosh.com/http-service/api/login"
    ACTIVITIES_URL = "https://service14.mywhoosh.com/v2/rider/profile/activities"
    DOWNLOAD_URL = "https://service14.mywhoosh.com/v2/rider/profile/download-activity-file"

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.req = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
        self.accessToken = None
        self.whooshId = None

    def login(self):
        """Login to MyWhoosh and get access token."""
        login_data = {
            "Username": self.email,
            "Password": self.password,
            "Platform": "Android",
            "Action": 1001,
            "CorrelationId": str(uuid.uuid4()),
            "DeviceId": str(uuid.uuid4()),
            "Authorization": "",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
        }

        response = self.req.request('POST', self.LOGIN_URL, body=json.dumps(login_data), headers=headers)
        login_response = json.loads(response.data)

        if not login_response.get("Success") or not login_response.get("AccessToken"):
            raise MyWhooshLoginError(
                "MyWhoosh login failed: " + str(login_response.get("Message", "Unknown error"))
            )

        self.accessToken = login_response["AccessToken"]
        self.whooshId = login_response["WhooshId"]
        print(f"MyWhoosh login successful - WhooshId: {self.whooshId}")

    def checkToken(self):
        if self.accessToken is None:
            self.login()

    def getActivitiesPage(self, page):
        self.checkToken()
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Authorization": f"Bearer {self.accessToken}",
        }
        body = json.dumps({"sortDate": "DESC", "page": page})
        response = self.req.request('POST', self.ACTIVITIES_URL, body=body, headers=headers)
        return json.loads(response.data)

    def getAllActivities(self, max_pages=10):
        """Fetch activities across pages, newest first."""
        all_activities = []
        page = 1
        while page <= max_pages:
            try:
                result = self.getActivitiesPage(page)
            except Exception as err:
                print(f"MyWhoosh: getActivitiesPage({page}) error: {err}")
                break
            data = result.get("data", {}) or {}
            results = data.get("results", []) or []
            all_activities.extend(results)
            total_pages = data.get("totalPages", 1) or 1
            if page >= total_pages or not results:
                break
            page += 1
        return all_activities

    def getRecentActivities(self, days=3, types=None):
        """
        Get activities from the last N days.

        `types` is accepted for interface parity with IntervalsClient.getRecentActivities
        but isn't used to filter — MyWhoosh is a cycling-only platform, so every
        activity is effectively a Ride already.
        """
        cutoff = time.time() - days * 86400
        activities = self.getAllActivities()
        recent = [a for a in activities if float(a.get("date", 0) or 0) >= cutoff]
        return recent

    def downloadFitFile(self, activity, output_dir):
        """
        Download the FIT file for a MyWhoosh activity and save it zipped,
        mirroring IntervalsClient.downloadFitFile's contract (COROS expects a zip).

        Args:
            activity: activity dict as returned by getRecentActivities/getAllActivities
                      (needs "id" and "activityFileId")
            output_dir: directory to save the zip into

        Returns:
            Path to the saved zip file, or None on failure
        """
        self.checkToken()
        activity_id = activity.get("id")
        file_id = activity.get("activityFileId")
        if not file_id:
            print(f"MyWhoosh: activity {activity_id} has no activityFileId, skipping")
            return None

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Authorization": f"Bearer {self.accessToken}",
        }
        body = json.dumps({"fileId": file_id})

        try:
            response = self.req.request('POST', self.DOWNLOAD_URL, body=body, headers=headers)
            result = json.loads(response.data)
            download_url = result.get("data")
            if not download_url:
                print(f"MyWhoosh: no download URL for activity {activity_id}: {result}")
                return None

            fit_response = self.req.request('GET', download_url)
            if fit_response.status != 200:
                print(f"MyWhoosh: failed to fetch FIT bytes for {activity_id} (HTTP {fit_response.status})")
                return None
            file_data = fit_response.data

            fit_filename = f"mw_{activity_id}.fit"
            zip_filename = f"mw_{activity_id}.zip"
            zip_path = os.path.join(output_dir, zip_filename)

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(fit_filename, file_data)

            print(f"MyWhoosh: Downloaded {activity_id} -> {zip_path} ({os.path.getsize(zip_path)} bytes)")
            return zip_path

        except Exception as err:
            print(f"MyWhoosh: downloadFitFile error for {activity_id}: {err}")
            return None


class MyWhooshLoginError(Exception):
    def __init__(self, status):
        super(MyWhooshLoginError, self).__init__(status)
        self.status = status
