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
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _norm_text(s: str) -> str:
        return "".join(str(s).split())

    @staticmethod
    def _require_pil():
        try:
            from PIL import Image, ImageChops, ImageStat, ImageOps
            return Image, ImageChops, ImageStat, ImageOps
        except ImportError as e:
            raise RuntimeError("word 点选需要 Pillow，请 pip install Pillow") from e

    @classmethod
    def _open_rgb(cls, data: bytes):
        Image, _, _, _ = cls._require_pil()
        im = Image.open(BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, im).convert("RGB")

    @classmethod
    def _glyph_variants(cls, data: bytes) -> list:
        """生成多种预处理小图，提高匹配/OCR 成功率。"""
        Image, _, _, ImageOps = cls._require_pil()
        variants = [data]
        try:
            im = Image.open(BytesIO(data)).convert("RGBA")
            # 白底
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            white = Image.alpha_composite(bg, im).convert("RGB")
            # 黑底
            bg_b = Image.new("RGBA", im.size, (0, 0, 0, 255))
            black = Image.alpha_composite(bg_b, im).convert("RGB")

            def pad_center(img, size=128):
                img = img.convert("RGB")
                w, h = img.size
                scale = min((size - 16) / max(w, 1), (size - 16) / max(h, 1), 4.0)
                scale = max(scale, 1.0)
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                img = img.resize((nw, nh), Image.Resampling.NEAREST)
                canvas = Image.new("RGB", (size, size), (255, 255, 255))
                canvas.paste(img, ((size - nw) // 2, (size - nh) // 2))
                return canvas

            for base in (white, black, ImageOps.invert(white)):
                for img in (base, pad_center(base, 128), pad_center(base, 64)):
                    buf = BytesIO()
                    img.save(buf, format="PNG")
                    variants.append(buf.getvalue())
        except Exception as e:
            print(f"【word glyph预处理警告】{e}")
        return variants

    @staticmethod
    def _center(bbox) -> list:
        x1, y1, x2, y2 = bbox
        return [float(x1 + x2) / 2.0, float(y1 + y2) / 2.0]

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

    @classmethod
    def _diff_score(cls, crop_rgb, templ_rgb) -> float:
        """平均像素差，越小越像。"""
        Image, ImageChops, ImageStat, _ = cls._require_pil()
        tw, th = templ_rgb.size
        if tw < 2 or th < 2:
            return 999.0
        crop = crop_rgb.resize((tw, th), Image.Resampling.BILINEAR)
        # 灰度比
        c = crop.convert("L")
        t = templ_rgb.convert("L")
        diff = ImageChops.difference(c, t)
        mean = ImageStat.Stat(diff).mean[0]
        # 再试反色模板（部分字黑底白字）
        from PIL import ImageOps
        t_inv = ImageOps.invert(t)
        diff2 = ImageChops.difference(c, t_inv)
        mean2 = ImageStat.Stat(diff2).mean[0]
        return min(mean, mean2)

    def _get_scene_boxes(self, client) -> List[list]:
        """detection 优先；失败用 select 的 bbox。"""
        boxes = []
        try:
            det = client.detection(self.imgs)
            if det:
                boxes = [list(map(int, b)) for b in det]
                print(f"【word detection】{len(boxes)} boxes")
        except Exception as e:
            print(f"【word detection失败】{e}")

        if not boxes:
            try:
                raw = client.select(self.imgs)
                for item in raw or []:
                    if isinstance(item, dict) and item:
                        bbox = next(iter(item.values()))
                        boxes.append(list(map(int, bbox)))
                print(f"【word select boxes】{len(boxes)} boxes")
            except Exception as e:
                print(f"【word select失败】{e}")
        return boxes

    def _match_ques_to_boxes(
        self, scene_rgb, glyph_bytes_list: List[bytes], boxes: List[list]
    ) -> Optional[List[List[float]]]:
        if not boxes or len(boxes) < len(glyph_bytes_list):
            print(f"【word框不足】boxes={len(boxes)}, ques={len(glyph_bytes_list)}")
            # 框少时仍尝试匹配
            if not boxes:
                return None

        glyphs = []
        for gb in glyph_bytes_list:
            try:
                glyphs.append(self._open_rgb(gb))
            except Exception as e:
                print(f"【word打开ques失败】{e}")
                return None

        used = set()
        results = []
        scores = []
        for gi, glyph in enumerate(glyphs):
            best_i, best_score = None, 1e9
            for bi, bbox in enumerate(boxes):
                if bi in used:
                    continue
                x1, y1, x2, y2 = bbox
                if x2 <= x1 or y2 <= y1:
                    continue
                crop = scene_rgb.crop((x1, y1, x2, y2))
                sc = self._diff_score(crop, glyph)
                if sc < best_score:
                    best_score, best_i = sc, bi
            if best_i is None:
                print(f"【word框匹配失败】ques_index={gi}")
                return None
            # 经验阈值：平均差 > 90 基本不像
            if best_score > 95:
                print(f"【word框匹配分过高】ques_index={gi}, score={best_score:.1f}")
                return None
            used.add(best_i)
            scores.append(round(best_score, 2))
            results.append(self._center(boxes[best_i]))

        print(f"【word框模板匹配】scores={scores}, positions={results}")
        return results

    def _full_scene_template(
        self, scene_rgb, glyph_bytes_list: List[bytes]
    ) -> Optional[List[List[float]]]:
        Image, ImageChops, ImageStat, _ = self._require_pil()
        scene_l = scene_rgb.convert("L")
        sw, sh = scene_l.size
        # 降采样加速
        scale = 1.0
        if max(sw, sh) > 320:
            scale = 320 / max(sw, sh)
            scene_s = scene_l.resize(
                (max(1, int(sw * scale)), max(1, int(sh * scale))),
                Image.Resampling.BILINEAR,
            )
        else:
            scene_s = scene_l

        ssw, ssh = scene_s.size
        used_rects = []
        results = []
        scores = []
        step = 3

        for gb in glyph_bytes_list:
            glyph = self._open_rgb(gb).convert("L")
            tw0, th0 = glyph.size
            best = None  # score, cx, cy, rect_orig
            for sc in (1.0, 0.8, 1.2, 0.65, 1.4):
                tw = max(6, int(tw0 * sc * scale))
                th = max(6, int(th0 * sc * scale))
                if tw >= ssw or th >= ssh:
                    continue
                t = glyph.resize((tw, th), Image.Resampling.BILINEAR)
                for y in range(0, ssh - th + 1, step):
                    for x in range(0, ssw - tw + 1, step):
                        rect = (x, y, x + tw, y + th)
                        # 跳过重叠
                        skip = False
                        for ur in used_rects:
                            if self._iou(rect, ur) > 0.3:
                                skip = True
                                break
                        if skip:
                            continue
                        crop = scene_s.crop(rect)
                        diff = ImageChops.difference(crop, t)
                        mean = ImageStat.Stat(diff).mean[0]
                        if best is None or mean < best[0]:
                            # 还原原图坐标
                            cx = (x + tw / 2) / scale
                            cy = (y + th / 2) / scale
                            rect_o = (
                                x / scale,
                                y / scale,
                                (x + tw) / scale,
                                (y + th) / scale,
                            )
                            best = (mean, cx, cy, rect_o)
            if best is None or best[0] > 70:
                print(f"【word全图模板失败】best={best}")
                return None
            scores.append(round(best[0], 2))
            results.append([best[1], best[2]])
            used_rects.append(best[3])

        print(f"【word全图模板匹配】scores={scores}, positions={results}")
        return results

    def _ocr_glyph(self, client, q_path: str, q_bytes: bytes) -> str:
        url = f"https://static.geetest.com/{q_path}"
        for payload in [url] + self._glyph_variants(q_bytes):
            try:
                text = self._norm_text(client.classification(payload))
                if text:
                    return text
            except Exception:
                pass
            try:
                items = client.select(payload)
                if isinstance(items, list) and items:
                    for item in items:
                        if isinstance(item, dict) and item:
                            lab = self._norm_text(next(iter(item.keys())))
                            if lab:
                                return lab
            except Exception:
                pass
        return ""

    def _solve_by_ocr(self, client, scene_rgb, glyph_bytes_list, boxes) -> List[List[float]]:
        targets = []
        for q, qb in zip(self.ques, glyph_bytes_list):
            targets.append(self._ocr_glyph(client, q, qb))
        print(f"【word ques OCR】{targets}")

        if not any(targets):
            raise RuntimeError(
                f"ques OCR 全空（请确认远程 /classification 对小图可用，或依赖模板匹配）。"
                f" ques={self.ques}"
            )

        labeled = []
        if boxes:
            for bbox in boxes:
                x1, y1, x2, y2 = bbox
                crop = scene_rgb.crop((x1, y1, x2, y2))
                buf = BytesIO()
                crop.save(buf, format="PNG")
                try:
                    lab = self._norm_text(client.classification(buf.getvalue()))
                except Exception:
                    lab = ""
                if lab:
                    labeled.append({"label": lab, "bbox": bbox})

        if not labeled:
            try:
                raw = client.select(self.imgs)
                for item in raw or []:
                    if isinstance(item, dict) and item:
                        k, v = next(iter(item.items()))
                        k = self._norm_text(k)
                        if k:
                            labeled.append({"label": k, "bbox": list(map(int, v))})
            except Exception as e:
                print(f"【word select OCR失败】{e}")

        used = set()
        results = []
        for t in targets:
            t = self._norm_text(t)
            if not t:
                raise RuntimeError(f"ques OCR 不完整: {targets}")
            hit = None
            for i, det in enumerate(labeled):
                if i in used:
                    continue
                lab = det["label"]
                if lab == t or t in lab or lab in t:
                    used.add(i)
                    hit = self._center(det["bbox"])
                    break
            if hit is None:
                raise RuntimeError(
                    f"word 匹配失败: target={t!r}, targets={targets}, labeled={labeled}"
                )
            results.append(hit)
        print(f"【word OCR匹配】positions={results}")
        return results

    def find_word_positions(self) -> List[List[float]]:
        from geeked.dddd_client import get_dddd_client

        self._require_pil()
        client = get_dddd_client()
        scene_rgb = self._open_rgb(self.imgs)
        print(f"【word】scene_size={scene_rgb.size}, ques_n={len(self.ques)}")

        glyph_bytes_list = []
        for q in self.ques:
            glyph_bytes_list.append(self.load_image(f"https://static.geetest.com/{q}"))

        boxes = self._get_scene_boxes(client)

        # 1) detection/select 框 × ques 模板比对（主路径，不依赖识字）
        try:
            pos = self._match_ques_to_boxes(scene_rgb, glyph_bytes_list, boxes)
            if pos and len(pos) == len(self.ques):
                return pos
        except Exception as e:
            print(f"【word框模板异常】{e}")

        # 2) 全图滑窗模板
        try:
            pos = self._full_scene_template(scene_rgb, glyph_bytes_list)
            if pos and len(pos) == len(self.ques):
                return pos
        except Exception as e:
            print(f"【word全图模板异常】{e}")

        # 3) OCR 兜底
        return self._solve_by_ocr(client, scene_rgb, glyph_bytes_list, boxes)
