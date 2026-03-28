import urllib3
import json
import gzip
import os
import certifi
from datetime import datetime, timedelta


class IntervalsClient:
    """Client for intervals.icu API to download activities and FIT files."""

    BASE_URL = "https://intervals.icu/api/v1"

    def __init__(self, athlete_id, api_key):
        self.athlete_id = athlete_id
        self.api_key = api_key
        self.req = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
        self.auth_header = urllib3.make_headers(
            basic_auth=f"API_KEY:{self.api_key}"
        )
        self.auth_header["Accept"] = "application/json"

    def getActivities(self, oldest, newest, types=None):
        """
        Get list of activities within a date range.
        
        Args:
            oldest: Start date string (YYYY-MM-DD)
            newest: End date string (YYYY-MM-DD)
            types: Optional list of activity types to filter (e.g., ["Ride", "VirtualRide"])
        
        Returns:
            List of activity dicts
        """
        url = f"{self.BASE_URL}/athlete/{self.athlete_id}/activities?oldest={oldest}&newest={newest}"
        
        try:
            response = self.req.request('GET', url, headers=self.auth_header)
            
            if response.status != 200:
                print(f"Intervals.icu: Failed to get activities (HTTP {response.status})")
                print(f"  Response: {response.data[:500]}")
                return []
            
            activities = json.loads(response.data)
            
            if types:
                activities = [a for a in activities if a.get("type") in types]
            
            return activities
            
        except Exception as e:
            print(f"Intervals.icu: Error getting activities - {e}")
            return []

    def getRecentActivities(self, days=3, types=None):
        """Get activities from the last N days."""
        newest = datetime.now().strftime("%Y-%m-%d")
        oldest = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return self.getActivities(oldest, newest, types)

    def downloadFitFile(self, activity_id, output_dir):
        """
        Download the original FIT file for an activity.
        The API returns it gzip compressed.
        
        Args:
            activity_id: intervals.icu activity ID (e.g., "i55751783")
            output_dir: Directory to save the file
        
        Returns:
            Path to the saved file, or None on failure
        """
        url = f"{self.BASE_URL}/activity/{activity_id}/file"
        
        headers = dict(self.auth_header)
        headers["Accept"] = "application/octet-stream"
        
        try:
            response = self.req.request('GET', url, headers=headers)
            
            if response.status != 200:
                print(f"Intervals.icu: Failed to download {activity_id} (HTTP {response.status})")
                return None
            
            file_data = response.data
            
            # The API returns gzip compressed data - decompress it
            try:
                decompressed = gzip.decompress(file_data)
                file_data = decompressed
            except Exception:
                # Not gzipped, use as-is
                pass
            
            # Determine file extension from the data
            if len(file_data) > 12 and file_data[8:12] == b'.FIT':
                ext = "fit"
            else:
                ext = "fit"  # Default to fit
            
            # Save as a zip file (COROS expects zip)
            import zipfile
            import io
            
            fit_filename = f"{activity_id}.{ext}"
            zip_filename = f"{activity_id}.zip"
            zip_path = os.path.join(output_dir, zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(fit_filename, file_data)
            
            print(f"Intervals.icu: Downloaded {activity_id} -> {zip_path} ({os.path.getsize(zip_path)} bytes)")
            return zip_path
            
        except Exception as e:
            print(f"Intervals.icu: Error downloading {activity_id} - {e}")
            return None
