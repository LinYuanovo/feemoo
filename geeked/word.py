import requests
from typing import List


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
            parsed.append({"label": str(label), "bbox": bbox})
        return parsed

    @staticmethod
    def _center(bbox) -> list:
        x1, y1, x2, y2 = bbox
        return [(x1 + x2) / 2.0, (y1 + y2) / 2.0]

    def _match_bbox(self, target: str, detections: list, used: set):
        t = self._norm_text(target)
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
            if t and (t in lab or lab in t):
                used.add(i)
                return self._center(det["bbox"])
        return None

    def find_word_positions(self) -> List[List[float]]:
        from geeked.dddd_client import get_dddd_client

        client = get_dddd_client()
        targets = []
        for q in self.ques:
            q_bytes = self.load_image(f"https://static.geetest.com/{q}")
            text = client.classification(q_bytes)
            targets.append(text)

        raw = client.select(self.imgs)
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
