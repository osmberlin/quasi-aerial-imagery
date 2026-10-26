# Local anonymisation of faces, persons and licence plates in GoPro MAX ERP frames (Apple Silicon, 24 GB)

Date: 2026-10-14. Research only. Nothing was installed and no weights were downloaded.

Status legend: **[V]** verified on a fetched primary page (PyPI, GitHub README, Hugging Face card, vendor docs). **[U]** unverified: from a search snippet, a third-party page, general knowledge, or an estimate. Runtime figures are estimates, not measurements.

## 1. Recommendation (one pipeline)

Cut each ERP frame into **8 horizon views plus 1 nadir view**, detect in pinhole space, map masks back to ERP, anonymise, and write ERP JPEGs.

| Item | Choice |
|---|---|
| Views | 8 × pinhole, HFOV = VFOV = 90°, pitch 0°, yaw 0,45,…,315°, 1280×1280 (covers lat −45°..+45°, 50 % overlap). 1 × nadir view, pitch −75°, 1280×1280 (photographer's body and shadow, which lie below −45°). |
| Resolution | 1280 px / 90° = 14.2 px/deg vs ERP 5760 px / 360° = 16 px/deg, so views are at near-native scale. |
| Faces | EgoBlur Gen2 face model `ego_blur_face_gen2.jit`, camera option `camera-rgb`, default threshold. **[V]** Apache-2.0 per Meta FAQ, ~400 MB, ~104 M parameters, Mac runs on CPU only. |
| Plates | EgoBlur Gen2 licence-plate model `ego_blur_lp_gen2.jit`, same download. **[V]** Apache-2.0 per Meta FAQ. Solid fill (not Gaussian) on plates, because a Gaussian on a 30 px plate can leave the shape legible. QC decides. |
| Persons | Ultralytics YOLO26s, COCO class 0 (`person`), `imgsz=1280`, `conf=0.15`. **[V]** YOLO26 is the newest Ultralytics family (docs say released Jan 2026). **[V]** Code and models are AGPL-3.0 or Enterprise: a note, not a blocker for publishing images. Weight auto-download URL and size **[U]**. |
| Blur | Faces and persons: Gaussian, sigma = max(8 px, 0.35 × box side) in ERP pixels. Plates: solid black. Masks dilated by ~10 % of box side before mapping back. |
| Output | ERP JPEG q=95, same size. Copy XMP/GPano and GPS EXIF from the source (for example `exiftool -tagsFromFile`) **[U]**. |

### Install (Python 3.12 venv; egoblur requires Python 3.10–3.12 **[V]**)

```bash
python3.12 -m venv ~/venvs/anon && source ~/venvs/anon/bin/activate
pip install egoblur ultralytics opencv-python-headless numpy
# EgoBlur weights: download by hand after accepting the licence (see section 3). Do not automate.
```

### Weight URLs

- EgoBlur Gen1/Gen2 models: **https://www.projectaria.com/tools/egoblur** **[V]**. Email-gated: you must submit an email and accept the model licence agreement yourself. The zip URLs were not extracted from the page **[U]**. Expected files: `ego_blur_face_gen2.jit`, `ego_blur_lp_gen2.jit` **[V]**.
- YOLO26s COCO: Ultralytics auto-download on first use **[U]** (URL and size not verified).

### Minimal detector sketch (detector calls only; projection and back-mapping are your code)

```python
# views: list of BGR 1280x1280 pinhole images from your ERP->pinhole code.
# Each returns a uint8 mask in view pixels (255 = anonymise). Map to ERP with your inverse remap.
import subprocess, cv2, numpy as np
from ultralytics import YOLO

PERSON = YOLO("yolo26s.pt")  # [U] weight fetched on first use

def egoblur_mask(bgr, kind):  # kind: "face" or "lp"
    # EgoBlur Gen2 CLI flags [V]. The blurred image's changed pixels are the mask.
    # Per-view CLI calls reload a ~400 MB model: time it, and prefer the Python API if the repo exposes one [U].
    cv2.imwrite("work/in.png", bgr)
    flag = "--face_model_path" if kind == "face" else "--lp_model_path"
    subprocess.run(["egoblur-gen2", "--camera_name", "camera-rgb", flag,
                    f"models/ego_blur_{kind}_gen2.jit", "--input_image_path", "work/in.png",
                    "--output_image_path", "work/out.png"], check=True)
    out = cv2.imread("work/out.png")
    return np.any(out != bgr, axis=2).astype(np.uint8) * 255

def person_mask(bgr):
    r = PERSON.predict(bgr, classes=[0], conf=0.15, imgsz=1280, device="mps", verbose=False)[0]
    m = np.zeros(bgr.shape[:2], np.uint8)
    for x1, y1, x2, y2 in r.boxes.xyxy.cpu().numpy().astype(int):  # [V] API shape, standard Ultralytics
        m[max(y1, 0):y2, max(x1, 0):x2] = 255
    return m

def detect_view(bgr):
    m = egoblur_mask(bgr, "face") | egoblur_mask(bgr, "lp") | person_mask(bgr)
    return m  # plates vs faces/persons are separated by a second mask run or by a per-class flag [U]
```

Notes on the sketch: `device="mps"` for Ultralytics is not in its README **[V-negative]**; fall back to `"cpu"` if it errors. The plate/face split (solid vs Gaussian) needs a per-class mask: keep the three masks separate and merge after the ERP mapping.

### Thresholds

- EgoBlur: use the default for `camera-rgb`. The page gives no number **[V]**. For QC, rerun at a lower threshold (search snippet suggests 0.1 **[U]**).
- Persons: 0.15 for recall. Plates: start at the detector default, lower if QC finds misses.

### Runtime (estimate, not measured)

4,950 pinhole views (550 frames × 9). EgoBlur on CPU is the dominant cost (likely 1–3 s per view, including per-call model load **[U]**). YOLO26s at 1280 on CPU ≈ 0.3–0.5 s (scaled from the vendor's 87 ms CPU ONNX at 640 **[V]** for yolo26s, hardware unstated). Total ≈ 2–4 h, mostly EgoBlur. Measure 20 views before committing.

## 2. Comparison of candidates

| Tool | Install | Weights (size, source) | Input | Runtime on Mac | Quality evidence | Licence (code / weights) | Maintenance |
|---|---|---|---|---|---|---|---|
| **Meta EgoBlur** (pypi `egoblur` 2.0.1) | `pip install egoblur` **[V]** | Gen2 face and LP `.jit` (TorchScript per filenames), ~400 MB each **[V]**. Email-gated **[V]**. | Aria RGB, pinhole-like. Image in/out CLI (PNG, JPEG) **[V]**. No stated resolution. | CPU only on Mac, "very slow" per page **[V]**. Mac listed as supported OS (MacOS-14/15) **[V]**. | No numbers on the page **[V]**. Snippet claims 96 %/98 % recall vs EgoBlur from CoopScenes **[U]**. | Apache-2.0, code and Gen2 models **[V]** | Released 2.0.1 on 2026-04-15 **[V]** |
| **deface** (pypi 1.5.0) | `pip install deface` **[V]** | CenterFace ONNX, bundled in the wheel **[V]**. Faces only. | Frames; `--scale` for >720p **[V]**. | ONNX Runtime CPU; `--ep` option **[V]**. Speed not stated **[U]**. | No benchmark numbers **[V]**. | MIT **[V]** | Last release 2023-10-15 **[V]**. Stale. |
| **understand.ai anonymizer** | `git clone`; Python 3.6 env; `pip install -r requirements.txt` **[V]** | Two TF `.pb` files (faces, plates) on Google Drive, size unknown **[V]** | Only jpg/png; not grayscale, not fisheye **[V]** | TF 1.x era, unlikely to run on Apple Silicon without rework **[U]** | None **[V]** | Apache-2.0 **[V]** | Archived 2022-04-11, read-only **[V]** |
| **Ultralytics YOLO26** (COCO person) | `pip install ultralytics` **[V]** | Auto-download **[U]**. yolo26s 48.6 mAP50-95 @640, CPU ONNX 87 ms **[V]** | Any; `imgsz` 640–1280 | `device=mps` not in README **[V-neg]**; CPU works | COCO numbers above **[V]** | AGPL-3.0 or Enterprise **[V]** | Active |
| **Ultralytics community plate weights**: `Koushim/yolov8-license-plate-detection` | HF `hf_hub_download` **[V]** | `best.pt`, size unknown **[V]** | 640×640 **[V]** | Ultralytics stack | No metrics; dataset not stated **[V]**. Code path uses a different author name `koushik-ai` **[V]**. | MIT metadata on HF **[V]** | Last update unknown **[V]** |
| **Community face weights**: `arnabdhar/YOLOv8-Face-Detection` | HF **[V]** | `model.pt`, size unknown **[V]** | Not stated | Ultralytics stack | No metrics; ~10k Roboflow faces, 100 epochs **[V]** | AGPL-3.0 metadata **[V]** | Unknown |
| **open-image-models** (pypi 0.6.0) | `pip install "open-image-models[onnx]"` **[V]** | Source not stated **[V]**. Six YOLOv9 plate models at 256–640 px. | Images via `create_detector(name).predict(img)` **[V]** | ONNX CPU; GPU extras **[V]** | Own-dataset mAP50-95: 0.772 for `yolo-v9-s-608`, 0.606 for `t-256` **[V]** | Code MIT **[V]**. Weights licence not stated **[V]** | Released 2026-07-27 **[V]**. |
| **fast-alpr** (pypi 0.4.0) | `pip install fast-alpr` plus `[onnx]` extra **[V]** | Downloads not documented **[V]** | Image path or array | ONNX CPU | OCR + detection, no anonymisation metrics | MIT **[V]** | 0.4.0 on 2026-03-15 **[V]** |
| **Street Viewer 360** (ForumViriumHelsinki) | `uv sync --extra anonymization`; `uv run street-viewer-360 download-models --target ./models` **[V]** | YOLOv8n-face and YOLOv11n-plate **[V]** | **GoPro MAX ERP** with XMP GPano **[V]** | Device flag `auto/cpu/cuda/mps`; default not stated **[V]** | "Best-effort" self-disclaimer **[V]** | **Not shown [V]** | 14 commits, 0 stars **[V]** |
| **panorama360-anonymizer** | Python 3.14 uv env; plus a Python 3.12 conda env for NullFace **[V]** | YOLOv8-face, SCRFD (insightface), YOLOv8-plate, NullFace SD1.5 4–5 GB **[V]** | 360° ERP **[V]** | CUDA only tested; no MPS **[V]** | No ground truth; 892 photos run **[V]** | **AGPL-3.0 [V]**. InsightFace buffalo_l non-commercial note **[V]** | 2 commits **[V]** |
| **Mapillary** blurring | Not local | Server-side at upload (per forum thread title) **[U]** | n/a | not usable locally | n/a | n/a | n/a |

Other 2025–2026 items found and not usable as the core:
- **SVIA** (arXiv 2501.09393): street-view anonymisation for self-driving; MIT code per a third-party review **[U]**. Structure-level, not faces/plates only.
- **CoopScenes** (arXiv 2407.08261): claims 96 % face and 98 % plate recall, better than EgoBlur **[U]**, from a search snippet only.
- **YOLO27** is announced, not released **[V]**.

## 3. Licence and access notes

- EgoBlur gating: the page says "By submitting your email and accessing the EgoBlur model, you agree to abide by the model license agreement" **[V]**. Accepting it is up to the user.
- AGPL-3.0 (Ultralytics, the arnabdhar face weights, panorama360-anonymizer). Flagged, not rejected. The output images are not AGPL code. This repository does not include them; they are installed as dependencies of the anonymisation step only.
- Face datasets: WIDER FACE and InsightFace weights are non-commercial in the panorama360 README **[V]**. Avoid those weights for a public pipeline.

## 4. Quality-check procedure

1. Ground truth: pick about 60 frames across the 9 scenes (weighted by scene size). Label faces, persons and plates at native resolution (CVAT or similar, your own machine). Report per-class recall at IoU ≥0.3 against the mask.
2. Adversarial check: run **fast-alpr** (MIT) on each anonymised ERP frame and on its 9 pinhole views. Any read plate string with confidence above 0.5 is a failure. Run EgoBlur again at threshold 0.05 on the anonymised views: detections inside masked areas count as residual.
3. Visual sheets: a contact sheet of 1 frame in 20, crop every detection site at 100 %. Look for readable plates and faces.
4. Acceptance (proposed, to be agreed): 0 readable plates in the sample by fast-alpr; face and plate recall ≥ 95 % on the labelled sample; manual pass on any frame with a detection below threshold.
5. Record every run in `docs/log/RESULTS.md` and any decision in `docs/log/DECISIONS.md` (AGENTS.md rules).

## 5. Fallbacks

- EgoBlur gate refused or model fails on the Mac: `pip install deface` for faces (MIT, CenterFace, CPU). Plates via `pip install "open-image-models[onnx]"` with `yolo-v9-s-608-license-plate-end2end`, after verifying where its weights come from and their licence. Persons still via YOLO26s.
- Plates missed at 15 m: rerun the plate pass on each view upscaled ×1.5 (cost ×2.25), or use a second 640 px sweep with 2× the view count.
- Ultralytics AGPL unacceptable: torchvision `fasterrcnn_mobilenet_v3_large_fpn` COCO weights (BSD-3 code) **[U]**.
- Last resort: a human marks residual sites in the QC sheets and the frames are redone with manual boxes.

## 6. Not verified (summary)

- EgoBlur zip URLs and jit I/O contract beyond the CLI; per-view speed on M-series. **[U]**
- Ultralytics MPS support and the yolo26s weight URL and size. **[U]**
- open-image-models: weight source, weight licence and result attribute names. **[U]**
- Koushim plate model accuracy, dataset and date (metadata only). **[U]**
- Street Viewer 360 licence and weight sizes. **[U]**
- All runtime estimates in this note. **[U]**
- The CoopScenes recall claims. **[U]**

## Sources (fetched 2026-10-14)

- https://github.com/facebookresearch/EgoBlur
- https://pypi.org/project/egoblur/
- https://www.projectaria.com/tools/egoblur/
- https://pypi.org/project/deface/
- https://github.com/understand-ai/anonymizer
- https://github.com/ForumViriumHelsinki/street-viewer-360
- https://github.com/VladuceanuTudor/panorama360-anonymizer
- https://pypi.org/project/open-image-models/
- https://github.com/ankandrew/open-image-models
- https://pypi.org/project/fast-alpr/
- https://huggingface.co/arnabdhar/YOLOv8-Face-Detection
- https://huggingface.co/Koushim/yolov8-license-plate-detection
- https://github.com/ultralytics/ultralytics
- https://docs.ultralytics.com/models/yolo26/
- Search snippets only: https://arxiv.org/abs/2501.09393, https://arxiv.org/pdf/2407.08261, https://forum.mapillary.com/t/anonymize-blurring-faces-and-license-plates-before-upload/4230
