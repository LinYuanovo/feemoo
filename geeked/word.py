"""
Geetest v4 word（文字点选）— 抗旋转版。

核心：
1) detection/select 取框
2) 每个框：少量角度远程 OCR 投票（主路径，准）
3) 未命中：ques 小图多角度旋转后，与框内边缘做 NCC 模板匹配（本地 Pillow）
4) 坐标：center/W*10000, center/H*10000
"""
from io import BytesIO
from typing import List, Optional, Tuple, Dict

import requests

SCENE_W, SCENE_H = 300, 200

# OCR 角度（远程，控制次数）
OCR_ANGLES = (0, 20, -20, 40, -40, 60, -60, 90, -90, 120, -120, 150, -150, 180)
# 模板匹配角度（本地，可密）
TPL_ANGLES = tuple(range(-180, 180, 12))


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
    def _n(s: str) -> str:
        return "".join(str(s).split())

    @staticmethod
    def _pil():
        from PIL import Image, ImageOps, ImageFilter, ImageEnhance, ImageStat
        return Image, ImageOps, ImageFilter, ImageEnhance, ImageStat

    @classmethod
    def _rgb(cls, data: bytes):
        Image, *_ = cls._pil()
        im = Image.open(BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, im).convert("RGB")

    @staticmethod
    def _center(b):
        return [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0]

    @staticmethod
    def _ur(pos, w=SCENE_W, h=SCENE_H):
        return [int(round(pos[0] / w * 10000)), int(round(pos[1] / h * 10000))]

    @staticmethod
    def _iou(a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        ua = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        ub = max(0, bx2 - bx1) * max(0, by2 - by1)
        u = ua + ub - inter
        return inter / u if u else 0.0

    @staticmethod
    def _expand(b, sw, sh, r=0.22, mp=5):
        x1, y1, x2, y2 = b
        w, h = max(1, x2 - x1), max(1, y2 - y1)
        px, py = max(mp, int(w * r)), max(mp, int(h * r))
        return [max(0, x1 - px), max(0, y1 - py), min(sw, x2 + px), min(sh, y2 + py)]

    # ---------- OCR helpers ----------
    def _cls(self, client, payload) -> str:
        try:
            return self._n(client.classification(payload))
        except Exception:
            return ""

    def _to_png(self, im) -> bytes:
        buf = BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    def _ocr_ques(self, client, path: str, data: bytes) -> str:
        Image, *_ = self._pil()
        im = Image.open(BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(bg, im).convert("RGB")
        w, h = rgb.size
        if max(w, h) < 72:
            sc = max(2, 72 // max(w, h))
            rgb = rgb.resize((w * sc, h * sc))
        for p in (self._to_png(rgb), data, f"https://static.geetest.com/{path}"):
            t = self._cls(client, p)
            if t:
                return t
        return ""

    def _ocr_rotated(self, client, crop_rgb) -> Tuple[str, float]:
        """返回 (best_label, best_angle)。投票 + 正立加权。"""
        Image, *_ = self._pil()
        votes: Dict[str, float] = {}
        angle_for: Dict[str, float] = {}
        for a in OCR_ANGLES:
            rot = crop_rgb.rotate(
                a, resample=Image.Resampling.BILINEAR, expand=True, fillcolor=(255, 255, 255)
            )
            w, h = rot.size
            side = max(w, h, 84) + 12
            canvas = Image.new("RGB", (side, side), (255, 255, 255))
            canvas.paste(rot, ((side - w) // 2, (side - h) // 2))
            t = self._cls(client, self._to_png(canvas))
            if not t:
                continue
            wgt = 3.0 if a == 0 else (2.0 if abs(a) <= 40 else 1.0)
            votes[t] = votes.get(t, 0) + wgt
            # 记录最高权重角度
            if t not in angle_for or wgt >= 2:
                angle_for[t] = float(a)
        if not votes:
            return "", 0.0
        lab = max(votes.items(), key=lambda x: x[1])[0]
        return lab, angle_for.get(lab, 0.0)

    # ---------- edge + NCC template ----------
    @classmethod
    def _edge_norm(cls, im_l, size: int = 40):
        """灰度图 -> 固定尺寸边缘图，像素 0/1 float list。"""
        Image, ImageOps, ImageFilter, ImageEnhance, ImageStat = cls._pil()
        g = ImageEnhance.Contrast(im_l.convert("L")).enhance(2.0)
        g = ImageOps.contain(g, (size, size), Image.Resampling.BILINEAR)
        canvas = Image.new("L", (size, size), 255)
        ow, oh = g.size
        canvas.paste(g, ((size - ow) // 2, (size - oh) // 2))
        edge = canvas.filter(ImageFilter.FIND_EDGES)
        # 二值
        thr = max(12, ImageStat.Stat(edge).mean[0] * 0.8)
        edge = edge.point(lambda p, t=thr: 255 if p > t else 0)
        data = edge.tobytes()
        # 转 -1/1 中心化向量，便于 NCC
        vals = [1.0 if b > 127 else 0.0 for b in data]
        mean = sum(vals) / len(vals)
        vec = [v - mean for v in vals]
        norm = sum(v * v for v in vec) ** 0.5
        if norm < 1e-6:
            return None
        return [v / norm for v in vec]

    @classmethod
    def _glyph_edge_bank(cls, data: bytes, size: int = 40) -> Dict[int, list]:
        """ques 小图：白底后按角度旋转，预计算边缘向量 bank[angle]=vec。"""
        Image, ImageOps, *_ = cls._pil()
        im = Image.open(BytesIO(data)).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        gray = Image.alpha_composite(bg, im).convert("L")
        # 裁墨迹
        a = im.split()[3]
        mask = Image.new("L", im.size, 0)
        gp, ap, mp = gray.load(), a.load(), mask.load()
        w, h = im.size
        for y in range(h):
            for x in range(w):
                if ap[x, y] > 15 and gp[x, y] < 245:
                    mp[x, y] = 255
        bb = mask.getbbox() or gray.getbbox()
        if bb:
            gray = gray.crop(bb)
        bank = {}
        for a in TPL_ANGLES:
            rot = gray.rotate(a, resample=Image.Resampling.BILINEAR, expand=True, fillcolor=255)
            vec = cls._edge_norm(rot, size)
            if vec:
                bank[a] = vec
        return bank

    @classmethod
    def _crop_edge(cls, crop_rgb, size: int = 40) -> Optional[list]:
        return cls._edge_norm(crop_rgb.convert("L"), size)

    @staticmethod
    def _ncc(a: list, b: list) -> float:
        return sum(x * y for x, y in zip(a, b))

    def _best_tpl(self, crop_vec, glyph_bank: Dict[int, list]) -> Tuple[float, int]:
        if not crop_vec or not glyph_bank:
            return -1.0, 0
        best, ba = -1.0, 0
        for ang, gvec in glyph_bank.items():
            s = self._ncc(crop_vec, gvec)
            if s > best:
                best, ba = s, ang
        return best, ba

    # ---------- boxes ----------
    def _boxes(self, client, sw, sh) -> List[list]:
        raw = []
        try:
            raw += [list(map(int, b)) for b in (client.detection(self.imgs) or [])]
        except Exception as e:
            print(f"【word detection失败】{e}")
        try:
            for it in client.select(self.imgs) or []:
                if isinstance(it, dict) and it:
                    raw.append(list(map(int, next(iter(it.values())))))
        except Exception as e:
            print(f"【word select失败】{e}")
        raw = [b for b in raw if b[2] > b[0] and b[3] > b[1]]
        raw.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
        out = []
        for b in raw:
            if any(self._iou(b, u) > 0.42 for u in out):
                continue
            out.append(self._expand(b, sw, sh))
        print(f"【word检测框】{len(out)}")
        return out

    def _match(self, client, scene, glyph_bytes, targets, boxes) -> Optional[List[list]]:
        n, m = len(targets), len(boxes)
        crops = [scene.crop(tuple(b)).convert("RGB") for b in boxes]

        # 1) 多角度 OCR
        labs, angs = [], []
        for c in crops:
            lab, ang = self._ocr_rotated(client, c)
            labs.append(lab)
            angs.append(ang)
        print(f"【word场景OCR】{list(zip(labs, [self._center(b) for b in boxes], angs))}")

        # 2) 边缘 NCC 矩阵
        banks = [self._glyph_edge_bank(gb) for gb in glyph_bytes]
        crop_vecs = [self._crop_edge(c) for c in crops]
        ncc = [[-1.0] * m for _ in range(n)]
        ncc_ang = [[0] * m for _ in range(n)]
        for ti in range(n):
            for bi in range(m):
                s, a = self._best_tpl(crop_vecs[bi], banks[ti])
                ncc[ti][bi] = s
                ncc_ang[ti][bi] = a

        used = set()
        result: Dict[int, list] = {}
        conf: Dict[int, str] = {}

        # Phase 1: 精确 OCR
        for ti, t in enumerate(targets):
            t = self._n(t)
            if not t:
                continue
            cands = [bi for bi in range(m) if bi not in used and labs[bi] == t]
            if not cands:
                continue
            # 同分用 NCC 最高
            best = max(cands, key=lambda bi: ncc[ti][bi])
            used.add(best)
            result[ti] = self._center(boxes[best])
            conf[ti] = f"ocr@{angs[best]}"

        # Phase 2: 模糊 OCR（含/被含）且 NCC 不太差
        for ti, t in enumerate(targets):
            if ti in result:
                continue
            t = self._n(t)
            if not t:
                continue
            cands = []
            for bi in range(m):
                if bi in used or not labs[bi]:
                    continue
                if t in labs[bi] or labs[bi] in t:
                    cands.append(bi)
            if not cands:
                continue
            best = max(cands, key=lambda bi: ncc[ti][bi])
            if ncc[ti][best] >= 0.15 or labs[best] == t:
                used.add(best)
                result[ti] = self._center(boxes[best])
                conf[ti] = f"ocr_fuzzy:{labs[best]}"

        # Phase 3: 纯 NCC 高置信
        for ti in range(n):
            if ti in result:
                continue
            ranked = sorted(
                ((ncc[ti][bi], bi) for bi in range(m) if bi not in used),
                reverse=True,
            )
            if not ranked:
                continue
            best_s, best_bi = ranked[0]
            second = ranked[1][0] if len(ranked) > 1 else -1.0
            # NCC 阈值：相关 > 0.35 且优于次优 0.05
            if best_s >= 0.35 and (best_s - second) >= 0.04:
                used.add(best_bi)
                result[ti] = self._center(boxes[best_bi])
                conf[ti] = f"ncc:{best_s:.2f}@{ncc_ang[ti][best_bi]}"
            elif best_s >= 0.45:
                used.add(best_bi)
                result[ti] = self._center(boxes[best_bi])
                conf[ti] = f"ncc_hi:{best_s:.2f}"

        # Phase 4: 有 ≥2 个 OCR 锚点时，才允许较高 NCC 补最后一个
        ocr_anchor = sum(1 for v in conf.values() if v.startswith("ocr"))
        if len(result) == n - 1 and ocr_anchor >= 2:
            for ti in range(n):
                if ti in result:
                    continue
                ranked = sorted(
                    ((ncc[ti][bi], bi) for bi in range(m) if bi not in used),
                    reverse=True,
                )
                if ranked and ranked[0][0] >= 0.32:
                    best_s, best_bi = ranked[0]
                    second = ranked[1][0] if len(ranked) > 1 else -1.0
                    if best_s - second >= 0.03:
                        used.add(best_bi)
                        result[ti] = self._center(boxes[best_bi])
                        conf[ti] = f"ncc_fill:{best_s:.2f}"

        print(f"【word分配】{conf}")
        if len(result) != n:
            return None
        positions = [result[i] for i in range(n)]
        if len({(round(p[0]), round(p[1])) for p in positions}) != n:
            print(f"【word】重复坐标 {positions}")
            return None
        return positions

    def find_word_positions(self) -> List[List[float]]:
        from geeked.dddd_client import get_dddd_client

        client = get_dddd_client()
        scene = self._rgb(self.imgs)
        sw, sh = scene.size
        print(f"【word】scene={sw}x{sh}, ques_n={len(self.ques)}")

        glyph_bytes = [
            self.load_image(f"https://static.geetest.com/{q}") for q in self.ques
        ]
        targets = [self._ocr_ques(client, q, gb) for q, gb in zip(self.ques, glyph_bytes)]
        print(f"【word目标字】{targets}")
        if not all(targets):
            raise RuntimeError(f"ques OCR 不完整: {targets}")

        boxes = self._boxes(client, sw, sh)
        if len(boxes) < len(self.ques):
            raise RuntimeError(f"word 检测框不足: {len(boxes)}<{len(self.ques)}")

        pos = self._match(client, scene, glyph_bytes, targets, boxes)
        if not pos:
            raise RuntimeError(
                f"word 旋转增强失败: targets={targets}, boxes={len(boxes)}"
            )

        ur = [self._ur(p, sw, sh) for p in pos]
        print(f"【word像素】{pos} 【提交】{ur}")
        return ur
