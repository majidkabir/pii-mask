"""Images and PDFs for pii-mask: text out of PDFs, text and its position out of
images (OCR), and images with boxes drawn over personal data and faces blurred.

Everything runs on this machine. The libraries are imported on first use, so
the server starts without them; `status()` says whether they are installed.

    pdf(data)                  -> {"pages": [text, ...], "scanned": bool, "images": [png, ...]}
    ocr(data)                  -> {"width", "height", "lines": [{"text", "words": [{"text", "box"}]}]}
    redact(data, boxes, faces) -> {"data": png, "width", "height", "faces": n}

`data` is the file's bytes; images come back as PNG bytes, which carry no
metadata (camera, GPS position, author).
"""

import io
import os
import threading

# Latin-script languages EasyOCR reads with one model (latin_g2).
OCR_LANGS = ["en", "da", "no", "sv", "de", "nl", "fr", "es", "it", "pt", "pl"]
YUNET = "face_detection_yunet_2023mar.onnx"
YUNET_URL = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/" + YUNET
MAX_SIDE = 1568  # Claude scales larger images down anyway; smaller is faster to read
MAX_PDF_CHARS = 200_000
SCANNED_CHARS_PER_PAGE = 20  # less text than this per page: a scan, read as images


def models_dir(cache):
    return os.path.join(cache, "models")


class Media:
    def __init__(self, cache):
        self.cache = cache
        self.lock = threading.Lock()  # one OCR or face run at a time
        self._reader = None
        self._faces = None
        self._status = None
        self._checked = 0.0

    # ------------------------------------------------------------ status

    def status(self):
        """Which parts are installed: {"pdf": bool, "images": bool, "faces": bool}.
        Checked again at most every 30 s while something is missing (an install
        may be adding it), and never again once everything is there."""
        import time

        if self._status is not None and (all(self._status.values()) or time.time() - self._checked < 30):
            return self._status
        self._checked = time.time()
        self._status = self._check()
        return self._status

    def _check(self):
        def has(*mods):
            try:
                for m in mods:
                    __import__(m)
                return True
            except Exception:
                return False

        ocr_models = os.path.isdir(os.path.join(models_dir(self.cache), "easyocr"))
        return {
            "pdf": has("pypdf", "pypdfium2", "PIL"),
            "images": has("easyocr", "PIL", "numpy") and ocr_models,
            "faces": has("cv2", "PIL", "numpy") and os.path.exists(os.path.join(models_dir(self.cache), YUNET)),
        }

    def warm_up(self):
        """Loads the OCR model in the background, so the first image doesn't wait for it."""
        def run():
            try:
                self.reader()
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    # --------------------------------------------------------------- pdf

    def pdf(self, data, render=False, max_pages=5):
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("the PDF is encrypted")
        pages = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
            total += len(text)
            if total > MAX_PDF_CHARS:
                break
        # Form fields hold what someone filled in: often personal data, so it is text to mask.
        try:
            fields = reader.get_fields() or {}
            filled = [f"{k}: {v.get('/V')}" for k, v in fields.items() if v.get("/V") not in (None, "")]
            if filled:
                pages.append("Form fields:\n" + "\n".join(filled))
        except Exception:
            pass
        count = len(reader.pages)
        visible = sum(len("".join(t.split())) for t in pages)
        scanned = visible < SCANNED_CHARS_PER_PAGE * max(1, count)
        images = []
        if scanned and render:
            import pypdfium2

            doc = pypdfium2.PdfDocument(data)
            for i in range(min(len(doc), max_pages)):
                pil = doc[i].render(scale=2).to_pil()
                images.append(png(fit(pil)))
        return {"pages": pages, "page_count": count, "scanned": scanned, "truncated": total > MAX_PDF_CHARS, "images": images}

    # --------------------------------------------------------------- ocr

    def reader(self):
        if self._reader is None:
            import easyocr

            self._reader = easyocr.Reader(
                OCR_LANGS, gpu=False, model_storage_directory=os.path.join(models_dir(self.cache), "easyocr"),
                download_enabled=False, verbose=False,
            )
        return self._reader

    def ocr(self, data):
        """Text in an image, word by word with each word's box, grouped into lines.
        Coordinates are in the image as `redact` will return it (scaled to fit)."""
        import numpy as np

        img = fit(open_image(data))
        with self.lock:
            # width_ths=0: a box per word, so a redaction box covers only the word it should.
            found = self.reader().readtext(np.array(img), paragraph=False, width_ths=0.0, detail=1)
        words = []
        for box, text, conf in found:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            words.append({"text": text, "box": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))], "conf": round(float(conf), 3)})
        return {"width": img.width, "height": img.height, "lines": group_lines(words)}

    # ------------------------------------------------------------ redact

    def faces(self, img):
        import cv2
        import numpy as np

        if self._faces is None:
            self._faces = cv2.FaceDetectorYN.create(os.path.join(models_dir(self.cache), YUNET), "", (320, 320), 0.7)
        arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        with self.lock:
            self._faces.setInputSize((img.width, img.height))
            _, found = self._faces.detect(arr)
        out = []
        for f in found if found is not None else []:
            x, y, w, h = (int(v) for v in f[:4])
            pad = int(0.15 * max(w, h))
            out.append([max(0, x - pad), max(0, y - pad), min(img.width, x + w + pad), min(img.height, y + h + pad)])
        return out

    def redact(self, data, boxes, blur_faces=False):
        """Draws a dark box over each of `boxes` ({"box": [x0, y0, x1, y1], "label"}),
        pixelates faces when asked, and returns the image as PNG."""
        from PIL import ImageDraw

        img = fit(open_image(data))
        face_boxes = self.faces(img) if blur_faces else []
        for x0, y0, x1, y1 in face_boxes:
            region = img.crop((x0, y0, x1, y1))
            small = region.resize((max(1, (x1 - x0) // 24), max(1, (y1 - y0) // 24)))
            img.paste(small.resize(region.size), (x0, y0))
        draw = ImageDraw.Draw(img)
        for b in boxes:
            x0, y0, x1, y1 = (int(v) for v in b["box"])
            draw.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], fill=(28, 28, 32))
            label = b.get("label") or ""
            if label:
                size = max(9, min(18, int((y1 - y0) * 0.6)))
                font = label_font(size)
                if draw.textlength(label, font=font) <= (x1 - x0) + 4:
                    draw.text((x0 + 2, y0 + max(0, ((y1 - y0) - size) // 2)), label, fill=(240, 240, 240), font=font)
        return {"data": png(img), "width": img.width, "height": img.height, "faces": len(face_boxes)}

    def to_pdf(self, images):
        """A PDF of the given PNG pages: a scanned PDF rebuilt from its redacted pages."""
        pages = [open_image(i) for i in images]
        buf = io.BytesIO()
        pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
        return buf.getvalue()


# ------------------------------------------------------------------ helpers

def open_image(data):
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # as the camera meant it, before the metadata is dropped
    return img.convert("RGB")


def fit(img, side=MAX_SIDE):
    scale = side / max(img.width, img.height)
    if scale >= 1:
        return img
    return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))


def png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)  # PNG from pixels alone: no EXIF, no GPS
    return buf.getvalue()


def label_font(size):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow before 10.1
        return ImageFont.load_default()


def group_lines(words):
    """Words on the same line (their boxes overlap vertically by half), left to right.
    A line's text is its words joined with spaces; each word keeps its box and its
    offset in the line's text."""
    lines = []
    for w in sorted(words, key=lambda w: (w["box"][1] + w["box"][3]) / 2):
        x0, y0, x1, y1 = w["box"]
        h = y1 - y0
        for line in lines:
            ly0, ly1 = line["y0"], line["y1"]
            if min(y1, ly1) - max(y0, ly0) > 0.5 * min(h, ly1 - ly0):
                line["words"].append(w)
                line["y0"], line["y1"] = min(ly0, y0), max(ly1, y1)
                break
        else:
            lines.append({"y0": y0, "y1": y1, "words": [w]})
    out = []
    for line in sorted(lines, key=lambda l: l["y0"]):
        ws = sorted(line["words"], key=lambda w: w["box"][0])
        text, placed = "", []
        for w in ws:
            if text:
                text += " "
            placed.append({"text": w["text"], "box": w["box"], "start": len(text)})
            text += w["text"]
        out.append({"text": text, "words": placed})
    return out
