import base64
import io
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


def resolve_mode() -> str:
    """local / cloud。DDDD_MODE 显式指定；auto 时配了远程地址走云端，否则本地 ddddocr。"""
    mode = (os.environ.get("DDDD_MODE") or "auto").strip().lower()
    if mode in ("local", "cloud"):
        return mode
    base = (os.environ.get("DDDD_API_BASE") or "").strip()
    legacy = (os.environ.get("DDDD_SLIDE_URL") or "").strip()
    return "cloud" if (base or legacy) else "local"


def local_available() -> bool:
    try:
        import ddddocr  # noqa: F401
        return True
    except Exception:
        return False


class LocalDdddClient:
    """进程内 ddddocr，接口与 DdddClient 对齐（云端逻辑不受影响）。"""

    local = True

    def __init__(self, timeout: float | None = None):
        import ddddocr

        self._ddddocr = ddddocr
        self._ocr = ddddocr.DdddOcr(show_ad=False)
        self._det = ddddocr.DdddOcr(det=True, show_ad=False)
        self.timeout = timeout

    @staticmethod
    def _fetch(image) -> bytes:
        if image is None:
            raise ValueError("image is None")
        if isinstance(image, bytes):
            return image
        if isinstance(image, str):
            if image.startswith(("http://", "https://")):
                resp = requests.get(image, timeout=15)
                resp.raise_for_status()
                return resp.content
            return base64.b64decode(image)
        raise TypeError(f"不支持的图片类型: {type(image)}")

    @staticmethod
    def _pil():
        from PIL import Image
        return Image

    @classmethod
    def _open_rgb(cls, data: bytes):
        Image = cls._pil()
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, im).convert("RGB")

    @classmethod
    def _png(cls, im) -> bytes:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    @classmethod
    def _white_png(cls, data: bytes, min_side: int = 48) -> bytes:
        Image = cls._pil()
        rgb = cls._open_rgb(data)
        w, h = rgb.size
        if max(w, h) < min_side:
            scale = max(2, (min_side + max(w, h) - 1) // max(w, h))
            rgb = rgb.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
        return cls._png(rgb)

    @staticmethod
    def _clean(text: str) -> str:
        text = "".join(str(text).split())
        cjk = [
            ch for ch in text
            if "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff"
        ]
        if cjk and len(set(cjk)) == 1 and len(cjk) == len(text):
            return cjk[0]
        return text

    def _ocr_bytes(self, data: bytes) -> str:
        try:
            return self._clean(self._ocr.classification(data, png_fix=True))
        except Exception:
            return ""

    def classification(self, image) -> str:
        data = self._fetch(image)
        Image = self._pil()
        payloads = [self._white_png(data), data]
        for payload in payloads:
            text = self._ocr_bytes(payload)
            if text:
                return text
        rgb = self._open_rgb(data)
        from PIL import ImageFilter
        for size in (3, 5):
            med = rgb.filter(ImageFilter.MedianFilter(size))
            text = self._ocr_bytes(self._png(med))
            if text:
                return text
        return ""

    @staticmethod
    def _isolate(rgb, tol: int = 72):
        """按主色把字符从背景里抠出来（其余置白），点选小字抗背景干扰。"""
        from collections import Counter

        import numpy as np

        arr = np.asarray(rgb, dtype=np.uint8)
        quant = (arr // 24 * 24).reshape(-1, 3)
        out = []
        for color, _ in Counter(map(tuple, quant)).most_common(3):
            dom = np.array(color, dtype=np.float32)
            keep = np.abs(arr.astype(np.float32) - dom).sum(axis=2) <= tol
            if not (0.10 <= keep.mean() <= 0.65):
                continue
            iso = np.full_like(arr, 255)
            iso[keep] = arr[keep]
            out.append(LocalDdddClient._pil().fromarray(iso))
        return out

    def classification_votes(self, image, limit: int = 12) -> list:
        """本地专用：同一张图多种预处理下的识别候选（点选小字抗噪）。"""
        import cv2
        import numpy as np

        data = self._fetch(image)
        Image = self._pil()
        rgb = self._open_rgb(data)
        gray = np.array(rgb.convert("L"))
        otsu = Image.fromarray(
            cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        )
        from PIL import ImageFilter
        payloads = [
            self._white_png(data),
            data,
            self._png(rgb.filter(ImageFilter.MedianFilter(3))),
            self._png(rgb.filter(ImageFilter.MedianFilter(5))),
            self._png(rgb.resize((rgb.width * 2, rgb.height * 2), Image.Resampling.LANCZOS)),
            self._png(otsu),
        ]
        for iso in self._isolate(rgb):
            payloads.append(self._png(iso))
            payloads.append(self._png(iso.filter(ImageFilter.MedianFilter(3))))
        votes = []
        for payload in payloads[:limit]:
            text = self._ocr_bytes(payload)
            if text and text not in votes:
                votes.append(text)
        return votes

    def detection(self, image) -> list:
        data = self._fetch(image)
        Image = self._pil()
        rgb = self._open_rgb(data)
        boxes = []
        for scale in (1, 2):
            payload = data if scale == 1 else self._png(
                rgb.resize((rgb.width * scale, rgb.height * scale), Image.Resampling.LANCZOS)
            )
            try:
                got = self._det.detection(payload) or []
            except Exception:
                got = []
            boxes.extend([[v / scale for v in b] for b in got])
        uniq = []
        for bbox in boxes:
            bbox = list(map(int, bbox))
            if any(self._iou(bbox, u) > 0.5 for u in uniq):
                continue
            uniq.append(bbox)
        return uniq

    @staticmethod
    def _iou(a, b) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    def select(self, image) -> list:
        data = self._fetch(image)
        Image = self._pil()
        rgb = self._open_rgb(data)
        out = []
        for bbox in self.detection(data):
            crop = rgb.crop(tuple(bbox))
            label = self._ocr_bytes(self._png(crop))
            if not label:
                label = self._ocr_bytes(self._white_png(self._png(crop)))
            if label:
                out.append({label: bbox})
        return out

    def slide_gap(self, sliding_image, back_image) -> float:
        return float(self.capcode(sliding_image, back_image, simple_target=True))

    def capcode(self, sliding_image, back_image, simple_target: bool = True):
        piece = self._fetch(sliding_image)
        bg = self._fetch(back_image)
        left = self._masked_slide_left(piece, bg)
        if left is None:
            res = self._ocr.slide_match(piece, bg, simple_target=simple_target)
            target = res.get("target") or [0, 0]
            left = max(0.0, float(target[0]) - self._piece_width(piece) / 2)
        return left

    def slide_comparison(self, sliding_image, back_image):
        piece = self._fetch(sliding_image)
        bg = self._fetch(back_image)
        res = self._ocr.slide_comparison(piece, bg)
        target = res.get("target") or [0, 0]
        return max(0.0, float(target[0]) - self._piece_width(piece) / 2)

    def _piece_width(self, piece: bytes) -> int:
        Image = self._pil()
        im = Image.open(io.BytesIO(piece)).convert("RGBA")
        box = im.split()[-1].getbbox()
        if not box:
            return 1
        return max(1, box[2] - box[0])

    @staticmethod
    def _masked_slide_left(piece: bytes, bg: bytes):
        import numpy as np
        import cv2

        Image = LocalDdddClient._pil()
        p = Image.open(io.BytesIO(piece)).convert("RGBA")
        box = p.split()[-1].getbbox()
        if not box:
            return None
        p = p.crop(box)
        b = Image.open(io.BytesIO(bg)).convert("RGB")
        if p.width >= b.width or p.height >= b.height:
            return None
        templ = np.array(p.convert("RGB")).astype(np.float32)
        mask = (np.array(p.split()[-1]) > 40).astype(np.float32)
        back = np.array(b).astype(np.float32)
        res = cv2.matchTemplate(back, templ, cv2.TM_CCORR_NORMED, mask=mask)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < 0.2:
            return None
        return float(max_loc[0])


_client = None


def get_dddd_client():
    global _client
    if _client is None:
        if resolve_mode() == "local":
            if not local_available():
                raise ValueError("本地模式需要安装 ddddocr（pip install ddddocr），或设置 DDDD_API_BASE 走云端")
            _client = LocalDdddClient()
        else:
            _client = DdddClient()
    return _client
