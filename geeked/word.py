from io import BytesIO
from typing import List, Optional, Tuple

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
    def _open_rgb(data: bytes):
        from PIL import Image
        im = Image.open(BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, im).convert("RGB")

    @staticmethod
    def _center(bbox) -> list:
        x1, y1, x2, y2 = bbox
        return [(x1 + x2) / 2.0, (y1 + y2) / 2.0]

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

    def _ocr_one_glyph(self, client, q_path: str, q_bytes: bytes) -> str:
        url = f"https://static.geetest.com/{q_path}"
        for payload in (url, q_bytes):
            try:
                text = self._norm_text(client.classification(payload))
                if text:
                    return text
            except Exception:
                pass
        try:
            items = client.select(q_bytes)
            parsed = self._parse_select_items(items)
            if parsed:
                return self._norm_text(parsed[0]["label"])
        except Exception:
            pass
        return ""

    def _template_match_one(
        self,
        scene_gray,
        templ_gray,
        used_rects: list,
        step: int = 2,
    ) -> Optional[Tuple[float, float, float, tuple]]:
        """
        在 scene 上滑窗匹配 templ，返回 (cx, cy, score, rect)。
        score 越小越好（归一化 SAD）。跳过与已用区域重叠过多的位置。
        """
        from PIL import Image

        sw, sh = scene_gray.size
        tw, th = templ_gray.size
        if tw < 4 or th < 4 or tw >= sw or th >= sh:
            return None

        # 适当放大/缩小模板多尺度
        scales = [1.0]
        for s in (0.85, 1.15, 0.7, 1.3):
            nw, nh = int(tw * s), int(th * s)
            if 8 <= nw < sw and 8 <= nh < sh:
                scales.append(s)

        best = None  # (score, cx, cy, rect)
        scene_px = scene_gray.load()

        for scale in scales:
            if scale == 1.0:
                t_img = templ_gray
            else:
                t_img = templ_gray.resize(
                    (max(8, int(tw * scale)), max(8, int(th * scale))),
                    Image.Resampling.BILINEAR,
                )
            tw2, th2 = t_img.size
            t_px = t_img.load()
            n = tw2 * th2
            if n <= 0:
                continue
            # 模板均值，用于简化 NCC 近似
            t_sum = 0
            for yy in range(th2):
                for xx in range(tw2):
                    t_sum += t_px[xx, yy]
            t_mean = t_sum / n

            y_range = range(0, sh - th2 + 1, step)
            x_range = range(0, sw - tw2 + 1, step)
            for y in y_range:
                for x in x_range:
                    rect = (x, y, x + tw2, y + th2)
                    if self._overlaps(rect, used_rects, iou_thresh=0.35):
                        continue
                    # 归一化 SAD
                    sad = 0
                    s_sum = 0
                    for yy in range(th2):
                        for xx in range(tw2):
                            sv = scene_px[x + xx, y + yy]
                            tv = t_px[xx, yy]
                            sad += abs(sv - tv)
                            s_sum += sv
                    s_mean = s_sum / n
                    # 亮度差补偿
                    bias = abs(s_mean - t_mean)
                    score = (sad / n) + bias * 0.25
                    if best is None or score < best[0]:
                        cx = x + tw2 / 2.0
                        cy = y + th2 / 2.0
                        best = (score, cx, cy, rect)

        if best is None:
            return None
        score, cx, cy, rect = best
        return cx, cy, score, rect

    @staticmethod
    def _overlaps(rect, used_rects, iou_thresh=0.35) -> bool:
        if not used_rects:
            return False
        x1, y1, x2, y2 = rect
        a = max(0, x2 - x1) * max(0, y2 - y1)
        if a <= 0:
            return False
        for ux1, uy1, ux2, uy2 in used_rects:
            ix1, iy1 = max(x1, ux1), max(y1, uy1)
            ix2, iy2 = min(x2, ux2), min(y2, uy2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            b = max(0, ux2 - ux1) * max(0, uy2 - uy1)
            union = a + b - inter
            if union > 0 and inter / union >= iou_thresh:
                return True
        return False

    def _solve_by_template(self) -> Optional[List[List[float]]]:
        from PIL import Image

        scene = self._open_rgb(self.imgs).convert("L")
        # 过大图降采样加速
        sw, sh = scene.size
        scale_down = 1.0
        max_side = 360
        if max(sw, sh) > max_side:
            scale_down = max_side / max(sw, sh)
            scene = scene.resize(
                (max(1, int(sw * scale_down)), max(1, int(sh * scale_down))),
                Image.Resampling.BILINEAR,
            )

        used = []
        results = []
        scores = []
        for q in self.ques:
            q_bytes = self.load_image(f"https://static.geetest.com/{q}")
            templ = self._open_rgb(q_bytes).convert("L")
            if scale_down != 1.0:
                tw, th = templ.size
                templ = templ.resize(
                    (max(4, int(tw * scale_down)), max(4, int(th * scale_down))),
                    Image.Resampling.BILINEAR,
                )
            hit = self._template_match_one(scene, templ, used, step=2)
            if hit is None:
                return None
            cx, cy, score, rect = hit
            # 分数过高视为未匹配上（灰度 0-255）
            if score > 80:
                return None
            used.append(rect)
            scores.append(score)
            # 还原到原图像素坐标
            results.append([cx / scale_down, cy / scale_down])

        print(f"【word模板匹配】scores={scores}, positions={results}")
        return results

    def _solve_by_ocr(self, client) -> List[List[float]]:
        targets = []
        q_bytes_list = []
        for q in self.ques:
            qb = self.load_image(f"https://static.geetest.com/{q}")
            q_bytes_list.append(qb)
            targets.append(self._ocr_one_glyph(client, q, qb))

        if not all(targets):
            raise RuntimeError(f"ques OCR 不完整: {targets}")

        # detection 拿框 + 每框 classification，比 select 整图一次更稳
        detections = []
        try:
            boxes = client.detection(self.imgs)
            scene_im = self._open_rgb(self.imgs)
            for bbox in boxes or []:
                x1, y1, x2, y2 = [int(v) for v in bbox]
                crop = scene_im.crop((x1, y1, x2, y2))
                buf = BytesIO()
                crop.save(buf, format="PNG")
                try:
                    label = self._norm_text(client.classification(buf.getvalue()))
                except Exception:
                    label = ""
                if label:
                    detections.append({"label": label, "bbox": [x1, y1, x2, y2]})
        except Exception as e:
            print(f"【word detection路径失败】{e}")

        if not detections:
            raw = client.select(self.imgs)
            detections = self._parse_select_items(raw)

        used = set()
        results = []
        for t in targets:
            t = self._norm_text(t)
            pos = None
            for i, det in enumerate(detections):
                if i in used:
                    continue
                lab = self._norm_text(det["label"])
                if lab == t or (t and (t in lab or lab in t)):
                    used.add(i)
                    pos = self._center(det["bbox"])
                    break
            if pos is None:
                raise RuntimeError(
                    f"word 匹配失败: target={t!r}, targets={targets}, detections={detections}"
                )
            results.append(pos)
        print(f"【word OCR匹配】targets={targets}, positions={results}")
        return results

    def find_word_positions(self) -> List[List[float]]:
        from geeked.dddd_client import get_dddd_client

        # 1) 优先模板匹配（不依赖 OCR 识字，ques 图与场景字形同源）
        try:
            by_tpl = self._solve_by_template()
            if by_tpl and len(by_tpl) == len(self.ques):
                return by_tpl
        except Exception as e:
            print(f"【word模板匹配异常】{e}")

        # 2) OCR 兜底
        client = get_dddd_client()
        return self._solve_by_ocr(client)
