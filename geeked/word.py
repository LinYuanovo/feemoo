from io import BytesIO
from typing import List, Optional

import requests

# 飞猫 word 场景图固定 300x200；userresponse 为相对坐标 * 10000
SCENE_W = 300
SCENE_H = 200


class WordSolver:
    def __init__(self, imgs: str, ques: List[str]):
        self.imgs_url = f"https://static.geetest.com/{imgs}"
        self.imgs = self.load_image(self.imgs_url)
        self.ques = ques

    @staticmethod
    def load_image(url: str) -> bytes:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _norm_text(s: str) -> str:
        return "".join(str(s).split())

    @staticmethod
    def _require_pil():
        from PIL import Image
        return Image

    @classmethod
    def _to_white_png(cls, data: bytes, min_side: int = 64) -> bytes:
        Image = cls._require_pil()
        im = Image.open(BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(bg, im).convert("RGB")
        w, h = rgb.size
        if max(w, h) < min_side:
            scale = max(2, (min_side + max(w, h) - 1) // max(w, h))
            rgb = rgb.resize((w * scale, h * scale), Image.Resampling.NEAREST)
        buf = BytesIO()
        rgb.save(buf, format="PNG")
        return buf.getvalue()

    @classmethod
    def _open_rgb(cls, data: bytes):
        Image = cls._require_pil()
        im = Image.open(BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, im).convert("RGB")

    @staticmethod
    def _center(bbox) -> list:
        x1, y1, x2, y2 = bbox
        return [(x1 + x2) / 2.0, (y1 + y2) / 2.0]

    @staticmethod
    def _to_userresponse(pos: list, scene_w: int = SCENE_W, scene_h: int = SCENE_H) -> list:
        """像素中心 -> 极验 word 提交坐标（相对 * 10000）。"""
        x, y = pos
        return [int(round(x / scene_w * 10000)), int(round(y / scene_h * 10000))]

    @staticmethod
    def _char_sim(a: str, b: str) -> float:
        a, b = WordSolver._norm_text(a), WordSolver._norm_text(b)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if a in b or b in a:
            return 0.85
        sa, sb = set(a), set(b)
        inter = len(sa & sb)
        if inter:
            return 0.5 * inter / max(len(sa), len(sb))
        return 0.0

    def _ocr_png(self, client, png_bytes: bytes) -> str:
        try:
            return self._norm_text(client.classification(png_bytes))
        except Exception:
            return ""

    def _ocr_ques(self, client, q_path: str, q_bytes: bytes) -> str:
        white = self._to_white_png(q_bytes)
        for payload in (white, q_bytes, f"https://static.geetest.com/{q_path}"):
            if isinstance(payload, str):
                try:
                    text = self._norm_text(client.classification(payload))
                except Exception:
                    text = ""
            else:
                text = self._ocr_png(client, payload)
            if text:
                return text
        return ""

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

    def _scene_labeled_boxes(self, client) -> list:
        scene = self._open_rgb(self.imgs)
        labeled = []

        try:
            raw = client.select(self.imgs)
            for item in raw or []:
                if not isinstance(item, dict) or not item:
                    continue
                lab, bbox = next(iter(item.items()))
                lab = self._norm_text(lab)
                bbox = list(map(int, bbox))
                if not lab:
                    crop = scene.crop(tuple(bbox))
                    buf = BytesIO()
                    crop.save(buf, format="PNG")
                    lab = self._ocr_png(client, self._to_white_png(buf.getvalue()))
                if lab:
                    labeled.append({"label": lab, "bbox": bbox, "center": self._center(bbox)})
        except Exception as e:
            print(f"【word select失败】{e}")

        try:
            boxes = client.detection(self.imgs) or []
        except Exception as e:
            print(f"【word detection失败】{e}")
            boxes = []

        known = {tuple(x["bbox"]) for x in labeled}
        for bbox in boxes:
            bbox = list(map(int, bbox))
            if tuple(bbox) in known:
                continue
            if any(self._iou(bbox, x["bbox"]) > 0.5 for x in labeled):
                continue
            crop = scene.crop(tuple(bbox))
            buf = BytesIO()
            crop.save(buf, format="PNG")
            lab = self._ocr_png(client, buf.getvalue())
            if not lab:
                lab = self._ocr_png(client, self._to_white_png(buf.getvalue()))
            if lab:
                labeled.append({"label": lab, "bbox": bbox, "center": self._center(bbox)})

        print(f"【word场景字】{[(x['label'], x['center']) for x in labeled]}")
        return labeled

    def _assign(self, targets: List[str], labeled: list) -> Optional[List[list]]:
        n, m = len(targets), len(labeled)
        if m < 1:
            return None
        pairs = []
        for ti, t in enumerate(targets):
            for li, lab in enumerate(labeled):
                s = self._char_sim(t, lab["label"])
                if s > 0:
                    pairs.append((s, ti, li))
        pairs.sort(reverse=True)
        used_t, used_l = set(), set()
        result_map = {}
        for s, ti, li in pairs:
            if ti in used_t or li in used_l:
                continue
            # 精确优先：若还有精确匹配未用，不要用低分占位
            if s < 1.0:
                # 检查该 target 是否还有精确候选
                has_exact = any(
                    self._char_sim(targets[ti], labeled[j]["label"]) >= 1.0 and j not in used_l
                    for j in range(m)
                )
                if has_exact and s < 0.85:
                    continue
            used_t.add(ti)
            used_l.add(li)
            result_map[ti] = labeled[li]["center"]
            if len(used_t) == n:
                break
        if len(result_map) != n:
            return None
        return [result_map[i] for i in range(n)]

    def find_word_positions(self) -> List[List[float]]:
        from geeked.dddd_client import get_dddd_client

        self._require_pil()
        client = get_dddd_client()

        # 实际场景尺寸（一般 300x200）
        scene = self._open_rgb(self.imgs)
        scene_w, scene_h = scene.size

        targets = []
        for q in self.ques:
            qb = self.load_image(f"https://static.geetest.com/{q}")
            t = self._ocr_ques(client, q, qb)
            targets.append(t)
        print(f"【word目标字】{targets}")
        if not all(targets):
            raise RuntimeError(f"ques OCR 不完整: {targets}")

        labeled = self._scene_labeled_boxes(client)
        if not labeled:
            raise RuntimeError("场景未检出任何文字框")

        positions = self._assign(targets, labeled)
        if not positions:
            raise RuntimeError(
                f"word 匹配失败: targets={targets}, labeled={[x['label'] for x in labeled]}"
            )

        userresponse = [self._to_userresponse(p, scene_w, scene_h) for p in positions]
        print(f"【word像素中心】{positions}")
        print(f"【word点击坐标】{userresponse}")
        return userresponse
