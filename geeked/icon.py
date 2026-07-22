import random
import requests
from typing import List, Dict, Any


class IconSolver:
    ICON_MAPPING = {
        "8da090c135ff029f3b5e19f4c44f73c8.png": "u",
        "cb0eaa639b2117a69a81af3d8c1496a1.png": "d",
        "315ce8665e781dabcd1eb09d3e604803.png": "l",
        "38bd9dda695098c7dfad74c921923a7d.png": "lu",
        "502e51dbabf411beba2dcd55fd38ebbd.png": "ld",
        "2b2387f566f6a03ed594d4d7cfda471f.png": "r",
        "78dc29045d587ad054c7353732df53c5.png": "ru",
        "23ef93e6b0e0df0e15b66667c99a5fb4.png": "rd",
    }

    def __init__(self, imgs: str, ques: List[str]):
        self.imgs_url = f"https://static.geetest.com/{imgs}"
        self.imgs = self.load_image(self.imgs_url)
        self.ques = ques

    @staticmethod
    def load_image(url: str) -> bytes:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.content

    def _get_directions(self) -> List[Dict[str, Any]]:
        return [{"direction": self.ICON_MAPPING.get(q.split("/")[-1], "")} for q in self.ques]

    @staticmethod
    def _parse_select_items(items: list) -> list:
        parsed = []
        for item in items:
            if not isinstance(item, dict) or not item:
                continue
            label, bbox = next(iter(item.items()))
            label_norm = str(label).split("_")[-1]
            parsed.append({"label": label_norm, "bbox": bbox})
        return parsed

    def find_icon_position(self) -> List[List[float]]:
        from geeked.dddd_client import get_dddd_client

        client = get_dddd_client()
        raw = client.select(self.imgs)
        detections = self._parse_select_items(raw)

        box_directions = self._get_directions()
        unused_boxes = []
        results = []

        for det in detections:
            label = det["label"]
            x1, y1, x2, y2 = det["bbox"]
            center = [(x1 + (x2 - x1) / 2) * 33, (y1 + (y2 - y1) / 2) * 49]

            if label not in self.ICON_MAPPING.values():
                unused_boxes.append(center)
                continue

            matched = False
            for boxd in box_directions:
                if boxd["direction"] == label and "bbox" not in boxd:
                    boxd["bbox"] = center
                    matched = True
                    break
            if not matched:
                unused_boxes.append(center)

        for boxd in box_directions:
            if "bbox" in boxd:
                results.append(boxd["bbox"])
            elif unused_boxes:
                results.append(unused_boxes.pop(random.randint(0, len(unused_boxes) - 1)))

        if len(results) != len(box_directions):
            raise RuntimeError(
                f"icon select 不完整: 需要 {len(box_directions)}, 得到 {len(results)}, raw={raw}"
            )
        return results
