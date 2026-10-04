import urllib3
import json
import boto3
import certifi
from boto3.s3.transfer import TransferConfig
from oss.sts_token_error import StsTokenError
from utils.coros_oss_credients_utils import decode

# COROS moved the STS endpoint (Oct 2026): the public faq.coros.com/openapi/oss/sts
# now returns 404. Training Hub fetches credentials from {hub}/api/proxy/oss/sts,
# authenticated by the user's access token. The legacy URL is kept as a last fallback.
STS_PROXY_HOSTS = ["https://t.coros.com", "https://training.coros.com"]
LEGACY_STS_URL = "https://faq.coros.com/openapi/oss/sts"

REGION_ENDPOINTS = {
    "eu-coros": "https://s3.eu-central-1.amazonaws.com",
}
DEFAULT_ENDPOINT = "https://s3.us-west-1.amazonaws.com"


class AwsOssClient:
  def __init__(self, bucket="eu-coros", service="aws", app_id="1660188068672619112", sign="E34EF0E34A498A54A9C3EAEFC12B7CAF", v=2, access_token=None, hub_host=None):
    self.bucket = bucket
    self.service = service
    self.app_id = app_id
    self.sign = sign
    self.access_token = access_token
    self.hub_host = hub_host
    self.credentials = None
    self.access_key_id = None
    self.access_key_secret = None
    self.req = urllib3.PoolManager(cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
    self.v = v
    self.client = None
    self.initClient()

  def _get_json(self, url, headers=None):
    response = self.req.request('GET', url, headers=headers or {}, timeout=30)
    if response.status != 200:
      raise StsTokenError(f"HTTP {response.status} from {url.split('?')[0]}")
    try:
      return json.loads(response.data)
    except Exception:
      raise StsTokenError(f"Non-JSON response from {url.split('?')[0]}")

  def _fetch_sts(self):
    errors = []
    params = f"bucket={self.bucket}&service={self.service}&v={self.v}"
    if self.access_token:
      hosts = ([self.hub_host] if self.hub_host else []) + [h for h in STS_PROXY_HOSTS if h != self.hub_host]
      headers = {
        "Accept": "application/json, text/plain, */*",
        "accesstoken": self.access_token,
        "Cookie": f"CPL-coros-token={self.access_token}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      }
      for host in hosts:
        url = f"{host}/api/proxy/oss/sts?{params}"
        try:
          data = self._get_json(url, headers)
          if data.get("code") == 200:
            print(f"AWS OSS: STS token from {host}/api/proxy/oss/sts")
            return data
          errors.append(f"{host}: code={data.get('code')} msg={data.get('message') or data.get('msg')}")
        except Exception as e:
          errors.append(f"{host}: {e}")
    else:
      errors.append("no COROS access token passed; skipping /api/proxy/oss/sts")
    # Legacy public endpoint
    url = f"{LEGACY_STS_URL}?{params}&app_id={self.app_id}&sign={self.sign}"
    try:
      data = self._get_json(url)
      if data.get("code") == 200:
        print("AWS OSS: STS token from legacy faq.coros.com endpoint")
        return data
      errors.append(f"legacy: code={data.get('code')}")
    except Exception as e:
      errors.append(f"legacy: {e}")
    raise StsTokenError("Get AWS OSS STS Token failed -> " + " | ".join(errors))

  def initClient(self):
        print(f"AWS OSS: Initializing with bucket={self.bucket}")
        sts_token_response = self._fetch_sts()
        credentials = sts_token_response["data"]["credentials"]
        self.v = sts_token_response["data"].get("v", self.v)
        self.credentials = credentials
        credients_json = decode(credentials)

        # New proxy response carries Region/Bucket; prefer them when present
        if credients_json.get("Bucket"):
            self.bucket = credients_json["Bucket"]
        region = credients_json.get("Region")
        if region:
            endpoint_url = f"https://s3.{region}.amazonaws.com"
        else:
            endpoint_url = REGION_ENDPOINTS.get(self.bucket, DEFAULT_ENDPOINT)

        print(f"AWS OSS: Using endpoint {endpoint_url}, bucket={self.bucket}")

        self.client = boto3.client(
            "s3",
            aws_access_key_id=credients_json["AccessKeyId"],
            aws_secret_access_key=credients_json["SecretAccessKey"],
            aws_session_token=credients_json["SessionToken"],
            endpoint_url=endpoint_url,
        )

  def multipart_upload(self, filePath, fileName):
      # 配置上传选项
      config = TransferConfig(
          multipart_threshold=1024 * 1024 * 5,  # 分片上传的阈值（5MB）
          max_concurrency=4,                   # 并发数
          multipart_chunksize=1024 * 1024 * 5,  # 分片大小（5MB）
          use_threads=True                     # 使用多线程
      )
      # 执行上传
      try:
          print(f"AWS OSS: Uploading to bucket={self.bucket}, key=fit_zip/{fileName}")
          self.client.upload_file(
              filePath,
              Bucket=self.bucket,
              Key=f"fit_zip/{fileName}",
              Config=config
          )
          print(f"File {fileName} uploaded successfully!")
      except Exception as e:
          print(f"Upload failed: {e}")
          raise e
