from io import BytesIO
from typing import List

import requests


class WordSolver:
    def __init__(self, imgs: str, ques: List[str]):
        self.imgs_url = f"https://static.geetest.com/{imgs}"
        self.imgs = self.load_image(self.imgs_url)
        self.ques = ques

    @staticmethod
    def load_image(url: str) -> bytes:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _norm_text(s: str) -> str:
        return "".join(str(s).split())

    @staticmethod
    def _parse_select_items(items: list) -> list:
        parsed = []
        for item in items:
            if not isinstance(item, dict) or not item:
                continue
            label, bbox = next(iter(item.items()))
            label = str(label).strip()
            if not label:
                continue
            parsed.append({"label": label, "bbox": bbox})
        return parsed

    @staticmethod
    def _center(bbox) -> list:
        x1, y1, x2, y2 = bbox
        return [(x1 + x2) / 2.0, (y1 + y2) / 2.0]

    @staticmethod
    def _to_opaque_png(data: bytes) -> bytes:
        """透明/浅色底小图转白底，便于 ddddocr classification。"""
        try:
            from PIL import Image
        except ImportError:
            return data
        try:
            im = Image.open(BytesIO(data)).convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            composed = Image.alpha_composite(bg, im).convert("RGB")
            # 过小则放大，提高 OCR 成功率
            w, h = composed.size
            if max(w, h) < 48:
                scale = max(2, 64 // max(w, h, 1))
                composed = composed.resize((w * scale, h * scale))
            out = BytesIO()
            composed.save(out, format="PNG")
            return out.getvalue()
        except Exception:
            return data

    def _ocr_one_glyph(self, client, q_path: str) -> str:
        url = f"https://static.geetest.com/{q_path}"
        raw = self.load_image(url)
        opaque = self._to_opaque_png(raw)

        candidates = []
        for payload in (url, opaque, raw):
            try:
                text = client.classification(payload)
                text = self._norm_text(text)
                if text:
                    return text
                candidates.append(repr(text))
            except Exception as e:
                candidates.append(f"cls_err:{e}")

        # 单字图走 /select：det+cls 有时比直接 classification 稳
        for payload in (opaque, raw, url):
            try:
                items = client.select(payload)
                parsed = self._parse_select_items(items)
                if parsed:
                    # 取面积最大的框的 label
                    def area(d):
                        x1, y1, x2, y2 = d["bbox"]
                        return abs(x2 - x1) * abs(y2 - y1)

                    best = max(parsed, key=area)
                    text = self._norm_text(best["label"])
                    if text:
                        return text
                candidates.append(f"select={items!r}")
            except Exception as e:
                candidates.append(f"sel_err:{e}")

        raise RuntimeError(f"ques 小图 OCR 为空: path={q_path}, tries={candidates}")

    def _match_bbox(self, target: str, detections: list, used: set):
        t = self._norm_text(target)
        if not t:
            return None
        for i, det in enumerate(detections):
            if i in used:
                continue
            if self._norm_text(det["label"]) == t:
                used.add(i)
                return self._center(det["bbox"])
        for i, det in enumerate(detections):
            if i in used:
                continue
            lab = self._norm_text(det["label"])
            if t in lab or lab in t:
                used.add(i)
                return self._center(det["bbox"])
        return None

    def find_word_positions(self) -> List[List[float]]:
        from geeked.dddd_client import get_dddd_client

        client = get_dddd_client()
        targets = [self._ocr_one_glyph(client, q) for q in self.ques]

        raw = client.select(self.imgs)
        detections = self._parse_select_items(raw)
        if not detections:
            # 兜底：整图 URL 再 select 一次
            raw = client.select(self.imgs_url)
            detections = self._parse_select_items(raw)

        used = set()
        results = []
        for t in targets:
            pos = self._match_bbox(t, detections, used)
            if pos is None:
                raise RuntimeError(
                    f"word 匹配失败: target={t!r}, targets={targets}, detections={detections}"
                )
            results.append(pos)

        if len(results) != len(self.ques):
            raise RuntimeError(f"word 结果数量不符: {len(results)} vs {len(self.ques)}")
        return results
