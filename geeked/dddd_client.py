import base64
import os
from urllib.parse import urlparse

import requests


def resolve_api_base() -> str:
    base = (os.environ.get("DDDD_API_BASE") or "").strip().rstrip("/")
    if base:
        return base
    legacy = (os.environ.get("DDDD_SLIDE_URL") or "").strip()
    if not legacy:
        raise ValueError("请设置 DDDD_API_BASE（或旧版 DDDD_SLIDE_URL）")
    parsed = urlparse(legacy)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"无效的 DDDD_SLIDE_URL: {legacy}")
    return f"{parsed.scheme}://{parsed.netloc}"


def to_image_payload(image) -> str:
    if image is None:
        raise ValueError("image is None")
    if isinstance(image, bytes):
        return base64.b64encode(image).decode("utf-8")
    if isinstance(image, str):
        return image
    raise TypeError(f"不支持的图片类型: {type(image)}")


class DdddClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        self.base_url = (base_url or resolve_api_base()).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _post(self, path: str, payload: dict):
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(data["error"])
        return data

    def capcode(self, sliding_image, back_image, simple_target: bool = True):
        data = self._post("/capcode", {
            "slidingImage": to_image_payload(sliding_image),
            "backImage": to_image_payload(back_image),
            "simpleTarget": simple_target,
        })
        if not isinstance(data, dict) or data.get("result") is None:
            raise RuntimeError(f"capcode 空结果: {data}")
        return data["result"]

    def slide_comparison(self, sliding_image, back_image):
        data = self._post("/slideComparison", {
            "slidingImage": to_image_payload(sliding_image),
            "backImage": to_image_payload(back_image),
        })
        if not isinstance(data, dict) or data.get("result") is None:
            raise RuntimeError(f"slideComparison 空结果: {data}")
        return data["result"]

    def slide_gap(self, sliding_image, back_image):
        try:
            return self.capcode(sliding_image, back_image, simple_target=True)
        except Exception as first_err:
            try:
                return self.slide_comparison(sliding_image, back_image)
            except Exception as second_err:
                raise RuntimeError(
                    f"滑块识别失败: capcode={first_err}; slideComparison={second_err}"
                ) from second_err

    def select(self, image) -> list:
        data = self._post("/select", {"image": to_image_payload(image)})
        if not isinstance(data, list):
            raise RuntimeError(f"select 响应异常: {data}")
        return data

    def classification(self, image) -> str:
        data = self._post("/classification", {"image": to_image_payload(image)})
        if not isinstance(data, dict) or data.get("result") is None:
            raise RuntimeError(f"classification 空结果: {data}")
        # 服务端可能返回空字符串，交给上层重试/兜底
        return str(data["result"]).strip()

    def detection(self, image) -> list:
        data = self._post("/detection", {"image": to_image_payload(image)})
        if not isinstance(data, dict) or data.get("result") is None:
            raise RuntimeError(f"detection 空结果: {data}")
        return data["result"]


_client: DdddClient | None = None


def get_dddd_client() -> DdddClient:
    global _client
    if _client is None:
        _client = DdddClient()
    return _client
