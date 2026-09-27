"""
Geetest v4 word（文字点选）。

流程：
1. ques 小图白底后 classification 得目标字
2. 场景图 detection/select 得框与字
3. 按目标字匹配中心点
4. userresponse = center / 图宽高 * 10000
"""
from io import BytesIO
import math
from typing import List, Optional

import requests

SCENE_W, SCENE_H = 300, 200
ROTATION_ANGLES = (-15, 15, -30, 30, -45, 45, -60, 60)


class WordSolver:
    def __init__(self, imgs: str, ques: List[str]):
        self.imgs_url = f"https://static.geetest.com/{imgs}"
        self.imgs = self.load_image(self.imgs_url)
        self.ques = ques

    @staticmethod
    def load_image(url: str) -> bytes:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.content

    @staticmethod
    def _norm(s: str) -> str:
        return "".join(str(s).split())

    @staticmethod
    def _cjk_chars(value: str) -> list[str]:
        return [
            ch for ch in value
            if "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff"
        ]

    @classmethod
    def _clean_label(cls, value: str) -> str:
        label = cls._norm(value)
        cjk = cls._cjk_chars(label)
        return cjk[0] if len(cjk) == 1 else label

    @staticmethod
    def _has_cjk(value: str) -> bool:
        return bool(WordSolver._cjk_chars(value))

    @staticmethod
    def _pil():
        from PIL import Image
        return Image

    @classmethod
    def _open_rgb(cls, data: bytes):
        Image = cls._pil()
        im = Image.open(BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, im).convert("RGB")

    @classmethod
    def _to_white_png(cls, data: bytes, min_side: int = 64) -> bytes:
        Image = cls._pil()
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

    @staticmethod
    def _center(bbox) -> list:
        x1, y1, x2, y2 = bbox
        return [(x1 + x2) / 2.0, (y1 + y2) / 2.0]

    @staticmethod
    def _to_userresponse(pos: list, scene_w: int = SCENE_W, scene_h: int = SCENE_H) -> list:
        x, y = pos
        return [int(round(x / scene_w * 10000)), int(round(y / scene_h * 10000))]

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

    @staticmethod
    def _char_sim(a: str, b: str) -> float:
        a, b = WordSolver._norm(a), WordSolver._norm(b)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if a in b or b in a:
            longer = b if a in b else a
            cjk_count = sum(
                "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff"
                for ch in longer
            )
            if cjk_count == 1:
                return 0.85
        sa, sb = set(a), set(b)
        inter = len(sa & sb)
        if inter:
            return 0.5 * inter / max(len(sa), len(sb))
        return 0.0

    def _ocr_png(self, client, png_bytes: bytes) -> str:
        try:
            return self._clean_label(client.classification(png_bytes))
        except Exception:
            return ""

    def _ocr_ques(self, client, q_path: str, q_bytes: bytes) -> str:
        white = self._to_white_png(q_bytes)
        results = []
        first_cjk = ""
        for payload in (white, q_bytes, f"https://static.geetest.com/{q_path}"):
            try:
                if isinstance(payload, str):
                    text = self._clean_label(client.classification(payload))
                else:
                    text = self._ocr_png(client, payload)
            except Exception:
                text = ""
            if text:
                results.append(text)
                if len(self._cjk_chars(text)) == 1:
                    return text
                if self._has_cjk(text) and not first_cjk:
                    first_cjk = text

        # 没得到单个汉字时，再尝试旋转目标图。
        image = self._open_rgb(q_bytes)
        for angle in (-30, -15, 15, 30, 45, -45):
            rotated = image.rotate(angle, expand=True, fillcolor="white")
            buf = BytesIO()
            rotated.save(buf, format="PNG")
            text = self._ocr_png(client, buf.getvalue())
            if text:
                results.append(text)
                if len(self._cjk_chars(text)) == 1:
                    return text
                if self._has_cjk(text) and not first_cjk:
                    first_cjk = text
        return first_cjk or (results[0] if results else "")

    def _scene_labeled_boxes(self, client) -> list:
        scene = self._open_rgb(self.imgs)
        labeled = []

        try:
            raw = client.select(self.imgs)
            for item in raw or []:
                if not isinstance(item, dict) or not item:
                    continue
                lab, bbox = next(iter(item.items()))
                lab = self._clean_label(lab)
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
            labeled.append({"label": lab, "bbox": bbox, "center": self._center(bbox)})

        # IoU 去重（select 与 detection 可能重复）
        uniq = []
        for item in labeled:
            if any(self._iou(item["bbox"], u["bbox"]) > 0.5 for u in uniq):
                continue
            uniq.append(item)

        print(f"【word场景字】{[(x['label'], x['center']) for x in uniq]}")
        return uniq

    @staticmethod
    def _map_rotated_center(center, angle: int, source_size, rotated_size) -> list:
        x, y = center
        source_w, source_h = source_size
        rotated_w, rotated_h = rotated_size
        dx, dy = x - rotated_w / 2.0, y - rotated_h / 2.0
        rad = math.radians(angle)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        return [
            cos_a * dx - sin_a * dy + source_w / 2.0,
            sin_a * dx + cos_a * dy + source_h / 2.0,
        ]

    def _add_rotated_candidates(self, client, targets: List[str], labeled: list) -> list:
        scene = self._open_rgb(self.imgs)
        candidates = list(labeled)

        for angle in ROTATION_ANGLES:
            rotated = scene.rotate(angle, expand=True, fillcolor="white")
            buf = BytesIO()
            rotated.save(buf, format="PNG")
            try:
                raw = client.select(buf.getvalue()) or []
            except Exception as e:
                print(f"【word旋转识别失败】angle={angle}: {e}")
                continue

            added = []
            for item in raw:
                if not isinstance(item, dict) or not item:
                    continue
                label, bbox = next(iter(item.items()))
                label = self._clean_label(label)
                if not label or max(self._char_sim(t, label) for t in targets) < 0.85:
                    continue
                center = self._map_rotated_center(
                    self._center(bbox), angle, scene.size, rotated.size
                )
                if not (0 <= center[0] < scene.width and 0 <= center[1] < scene.height):
                    continue
                if any(
                    item["label"] == label
                    and math.dist(item["center"], center) < 15
                    for item in candidates
                ):
                    continue
                candidate = {"label": label, "center": center}
                candidates.append(candidate)
                added.append((label, [round(v, 1) for v in center]))

            if added:
                print(f"【word旋转候选】angle={angle}: {added}")
            if self._assign(targets, candidates):
                break

        return candidates

    def _add_rotated_box_candidates(self, client, targets: List[str], labeled: list) -> list:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from PIL import ImageFilter

        scene = self._open_rgb(self.imgs)
        pending = [
            item for item in labeled
            if not item["label"] or not any(
                self._char_sim(target, item["label"]) >= 0.85 for target in targets
            )
        ]
        votes = [{} for _ in pending]
        coarse_angles = (0, -15, 15, -30, 30, -45, 45, -60, 60)
        stages = (
            (((100, 1), (140, 3), (80, 5)), coarse_angles),
            (((60, 1), (120, 3), (160, 5)), coarse_angles),
            (
                ((60, 3), (80, 3), (100, 5), (120, 1), (140, 1), (160, 3)),
                (0, -10, 10, -20, 20, -30, 30, -40, 40, -50, 50, -60, 60),
            ),
        )

        def classify(task):
            item_index, saturation, filter_size, angle, png = task
            return item_index, saturation, filter_size, angle, self._ocr_png(client, png)

        for variants, angles in stages:
            tasks = []
            for item_index, item in enumerate(pending):
                x1, y1, x2, y2 = map(int, item["bbox"])
                crop = scene.crop((x1, y1, x2, y2))
                side = int(max(crop.size) * math.sqrt(2)) + 12
                for saturation, filter_size in variants:
                    mask = self._pil().new("L", crop.size, 255)
                    mask.putdata([
                        0 if max(pixel) - min(pixel) >= saturation else 255
                        for pixel in crop.getdata()
                    ])
                    if filter_size > 1:
                        mask = mask.filter(ImageFilter.MinFilter(filter_size))
                    square = self._pil().new("L", (side, side), 255)
                    square.paste(mask, ((side - mask.width) // 2, (side - mask.height) // 2))
                    for angle in angles:
                        rotated = square.rotate(angle, expand=False, fillcolor=255).resize(
                            (96, 96), self._pil().Resampling.NEAREST
                        )
                        buf = BytesIO()
                        rotated.save(buf, format="PNG")
                        tasks.append((
                            item_index, saturation, filter_size, angle, buf.getvalue()
                        ))

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(classify, task) for task in tasks]
                for future in as_completed(futures):
                    item_index, saturation, filter_size, angle, label = future.result()
                    if not label or max(self._char_sim(t, label) for t in targets) < 0.85:
                        continue
                    record = votes[item_index].setdefault(label, {"count": 0, "params": None})
                    record["count"] += 1
                    if record["params"] is None:
                        record["params"] = (saturation, filter_size, angle)

            candidates = list(labeled)
            for item, item_votes in zip(pending, votes):
                if not item_votes:
                    continue
                label, record = max(
                    item_votes.items(),
                    key=lambda pair: (pair[1]["count"], -targets.index(pair[0]) if pair[0] in targets else 0),
                )
                saturation, filter_size, angle = record["params"]
                candidates.append({
                    "label": label, "bbox": item["bbox"], "center": item["center"]
                })
                print(
                    f"【word框掩码候选】votes={record['count']}, sat={saturation}, "
                    f"filter={filter_size}, angle={angle}: "
                    f"{label} @ {[round(v, 1) for v in item['center']]}"
                )
            if self._assign(targets, candidates):
                return candidates

        return candidates

    def _assign(self, targets: List[str], labeled: list) -> Optional[List[list]]:
        n, m = len(targets), len(labeled)
        if m < 1:
            return None

        pairs = []
        for ti, t in enumerate(targets):
            for li, lab in enumerate(labeled):
                s = self._char_sim(t, lab["label"])
                if s >= 0.85:
                    pairs.append((s, ti, li))
        pairs.sort(key=lambda item: (-item[0], item[1], item[2]))

        used_t, used_l = set(), set()
        result_map = {}
        for s, ti, li in pairs:
            if ti in used_t or li in used_l:
                continue
            used_t.add(ti)
            used_l.add(li)
            result_map[ti] = labeled[li]["center"]
            if len(used_t) == n:
                break

        if len(result_map) != n:
            return None

        positions = [result_map[i] for i in range(n)]
        # 拒绝重复坐标
        keys = {(round(p[0], 1), round(p[1], 1)) for p in positions}
        if len(keys) != n:
            print(f"【word】重复坐标: {positions}")
            return None
        return positions

    def find_word_positions(self) -> List[List[float]]:
        from geeked.dddd_client import get_dddd_client

        self._pil()
        client = get_dddd_client()
        scene = self._open_rgb(self.imgs)
        scene_w, scene_h = scene.size
        print(f"【word】scene_size={scene_w}x{scene_h}, ques_n={len(self.ques)}")

        targets = []
        for q in self.ques:
            qb = self.load_image(f"https://static.geetest.com/{q}")
            targets.append(self._ocr_ques(client, q, qb))
        print(f"【word目标字】{targets}")
        if not all(targets):
            raise RuntimeError(f"ques OCR 不完整: {targets}")

        labeled = self._scene_labeled_boxes(client)
        if not labeled:
            raise RuntimeError("场景未检出任何文字框")

        positions = self._assign(targets, labeled)
        if not positions:
            labeled = self._add_rotated_candidates(client, targets, labeled)
            positions = self._assign(targets, labeled)
        if not positions:
            labeled = self._add_rotated_box_candidates(client, targets, labeled)
            positions = self._assign(targets, labeled)
        if not positions:
            raise RuntimeError(
                f"word 匹配失败: targets={targets}, labeled={[x['label'] for x in labeled]}"
            )

        userresponse = [self._to_userresponse(p, scene_w, scene_h) for p in positions]
        print(f"【word像素中心】{positions}")
        print(f"【word点击坐标】{userresponse}")
        return userresponse
