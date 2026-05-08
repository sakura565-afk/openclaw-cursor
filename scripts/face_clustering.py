#!/usr/bin/env python3
"""Cluster faces in an image archive.

Backends (``--backend``):

- ``face_recognition`` — requires ``dlib`` (often prebuilt wheels; on Windows without
  wheels try ``pip install pipwin`` then ``pipwin install dlib`` before
  ``pip install face_recognition``).
- ``insightface`` — Buffalo_L + ONNX Runtime (CPU wheels on Windows via PyPI).
- ``opencv_dnn`` — OpenCV SSD face detector and simple patch embeddings (no InsightFace).
- ``auto`` — tries the above in that order.

Environment:

- ``INSIGHTFACE_ROOT`` — optional model root passed to InsightFace when set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

import numpy as np
from PIL import Image


DEFAULT_EPS = 0.6
_OPENCV_DEPLOY_URL = (
    "https://raw.githubusercontent.com/opencv/opencv/4.10.0/samples/dnn/face_detector/deploy.prototxt"
)
_OPENCV_CAFFE_URL = (
    "https://raw.githubusercontent.com/opencv/opencv_3rdparty/"
    "dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000_fp16.caffemodel"
)
CACHE_FILENAME = ".face_clustering_cache.json"
CATALOG_FILENAME = "catalog.json"
FOLDERS_DIRNAME = "face_clusters"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass
class FaceRecord:
    file_path: Path
    face_index: int
    encoding: np.ndarray


@dataclass
class ClusterResult:
    clusters: list[list[FaceRecord]]
    noise: list[FaceRecord]
    eps: float


class FaceBackend(Protocol):
    def encode(self, image_path: Path) -> list[np.ndarray]:
        ...


class FaceRecognitionBackend:
    def __init__(self) -> None:
        import face_recognition  # type: ignore

        self._face_recognition = face_recognition

    def encode(self, image_path: Path) -> list[np.ndarray]:
        image = self._face_recognition.load_image_file(str(image_path))
        locations = self._face_recognition.face_locations(image)
        encodings = self._face_recognition.face_encodings(image, known_face_locations=locations)
        return [np.asarray(vector, dtype=float) for vector in encodings]


class InsightFaceBackend:
    def __init__(self) -> None:
        try:
            from insightface.app import FaceAnalysis  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "insightface is not installed. On most platforms: pip install insightface onnxruntime"
            ) from exc

        root = os.environ.get("INSIGHTFACE_ROOT", "").strip()
        kwargs: dict[str, Any] = {"name": "buffalo_l"}
        if root:
            kwargs["root"] = root
        try:
            self._app = FaceAnalysis(providers=["CPUExecutionProvider"], **kwargs)
        except TypeError:
            self._app = FaceAnalysis(**kwargs)
        self._app.prepare(ctx_id=-1)

    def encode(self, image_path: Path) -> list[np.ndarray]:
        rgb_image = np.array(Image.open(image_path).convert("RGB"))
        bgr_image = rgb_image[:, :, ::-1]
        faces = self._app.get(bgr_image)
        return [np.asarray(face.embedding, dtype=float) for face in faces]


class OpenCvDnnFaceBackend:
    """SSD face detection (DNN) when model files are available; else Haar cascades.

    Embeddings are L2-normalized flattened grayscale face patches (distinct scale from
    InsightFace; use ``--cluster-count`` or tune ``DEFAULT_EPS`` if clusters look wrong).
    """

    def __init__(self) -> None:
        import cv2  # type: ignore

        self._cv2 = cv2
        self._net: Any | None = None
        self._cascade: Any | None = None
        self._use_haar = False
        self._model_dir = _opencv_face_model_dir()

    def _ensure_dnn(self) -> bool:
        if self._net is not None:
            return True
        cv2 = self._cv2
        proto = self._model_dir / "deploy.prototxt"
        weights = self._model_dir / "res10_300x300_ssd_iter_140000_fp16.caffemodel"
        try:
            _download_if_missing(proto, _OPENCV_DEPLOY_URL)
            _download_if_missing(weights, _OPENCV_CAFFE_URL)
            self._net = cv2.dnn.readNetFromCaffe(str(proto), str(weights))
            return True
        except (OSError, urllib.error.URLError, RuntimeError):
            self._net = None
            return False

    def _ensure_haar(self) -> None:
        if self._cascade is None:
            path = self._cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self._cascade = self._cv2.CascadeClassifier(path)
            if self._cascade.empty():  # pragma: no cover
                raise RuntimeError("OpenCV Haar cascade for frontal faces is unavailable.")

    def _face_boxes(self, bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        cv2 = self._cv2
        h, w = bgr.shape[:2]
        if not self._use_haar:
            if self._ensure_dnn():
                blob = cv2.dnn.blobFromImage(
                    bgr,
                    scalefactor=1.0,
                    size=(300, 300),
                    mean=(104.0, 117.0, 123.0),
                    swapRB=False,
                    crop=False,
                )
                net = self._net
                if net is None:  # pragma: no cover - defensive
                    self._use_haar = True
                else:
                    net.setInput(blob)
                    det = net.forward()
                    boxes: list[tuple[int, int, int, int]] = []
                    for i in range(det.shape[2]):
                        conf = float(det[0, 0, i, 2])
                        if conf < 0.5:
                            continue
                        x1, y1, x2, y2 = (det[0, 0, i, 3:7] * np.array([w, h, w, h])).astype(int)
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(w - 1, x2), min(h - 1, y2)
                        if x2 > x1 and y2 > y1:
                            boxes.append((x1, y1, x2, y2))
                    return boxes
            self._use_haar = True

        self._ensure_haar()
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        found = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
        boxes = []
        for (x, y, fw, fh) in found:
            boxes.append((int(x), int(y), int(x + fw), int(y + fh)))
        return boxes

    def encode(self, image_path: Path) -> list[np.ndarray]:
        rgb_image = np.array(Image.open(image_path).convert("RGB"))
        bgr = rgb_image[:, :, ::-1].copy()
        out: list[np.ndarray] = []
        for x1, y1, x2, y2 in self._face_boxes(bgr):
            face = bgr[y1:y2, x1:x2]
            if face.size == 0:
                continue
            gray = self._cv2.cvtColor(face, self._cv2.COLOR_BGR2GRAY)
            small = self._cv2.resize(gray, (64, 64), interpolation=self._cv2.INTER_AREA)
            vec = small.astype(np.float64).ravel()
            mean = float(vec.mean())
            vec = vec - mean
            n = float(np.linalg.norm(vec))
            if n > 1e-9:
                vec /= n
            out.append(vec)
        return out


def _opencv_face_model_dir() -> Path:
    env = os.environ.get("FACE_CLUSTERING_MODEL_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "face_clustering" / "opencv_dnn_face"


def _download_if_missing(dest: Path, url: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return
    request = urllib.request.Request(url, headers={"User-Agent": "face_clustering/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 — fixed URLs
        dest.write_bytes(response.read())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cluster faces in a photo archive.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--scan", required=True, help="Path to recursively scan for images.")
    parser.add_argument(
        "--cluster-count",
        type=positive_int,
        default=None,
        help="Target number of clusters (auto-tunes distance threshold).",
    )
    parser.add_argument(
        "--min-samples",
        type=positive_int,
        default=2,
        help="Minimum number of faces required to form a cluster.",
    )
    parser.add_argument("--export-json", action="store_true", help="Write catalog.json output.")
    parser.add_argument("--export-folders", action="store_true", help="Create person_* folders with symlinks.")
    parser.add_argument(
        "--backend",
        choices=("auto", "face_recognition", "insightface", "opencv_dnn"),
        default="auto",
        help="Face detection/embedding backend.",
    )
    parser.add_argument(
        "--catalog-path",
        default=None,
        help="Explicit path to catalog.json (default: <scan>/catalog.json).",
    )
    parser.add_argument(
        "--cache-path",
        default=None,
        help=f"Path to cache file (default: <scan>/{CACHE_FILENAME}).",
    )
    parser.add_argument(
        "--folders-dir",
        default=None,
        help=f"Directory for exported symlink folders (default: <scan>/{FOLDERS_DIRNAME}).",
    )
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def pick_backend(name: str) -> FaceBackend:
    if name == "face_recognition":
        return FaceRecognitionBackend()
    if name == "insightface":
        return InsightFaceBackend()
    if name == "opencv_dnn":
        return OpenCvDnnFaceBackend()

    errors: list[str] = []
    for label, factory in (
        ("face_recognition", FaceRecognitionBackend),
        ("insightface", InsightFaceBackend),
        ("opencv_dnn", OpenCvDnnFaceBackend),
    ):
        try:
            return factory()
        except Exception as exc:  # pragma: no cover - depends on optional library presence
            errors.append(f"{label}: {exc}")
    raise RuntimeError(
        "No available face backend. Install face_recognition, insightface (onnxruntime), "
        "or opencv-python; or use --backend opencv_dnn. " + " | ".join(errors)
    )


def discover_images(scan_path: Path) -> list[Path]:
    return sorted(
        file_path
        for file_path in scan_path.rglob("*")
        if file_path.is_file() and file_path.suffix.lower() in IMAGE_SUFFIXES
    )


def file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def extract_records(
    image_paths: Iterable[Path],
    *,
    backend: FaceBackend,
    cache_payload: dict[str, Any],
    scan_root: Path,
) -> list[FaceRecord]:
    files_cache = cache_payload.setdefault("files", {})
    records: list[FaceRecord] = []

    for image_path in image_paths:
        rel = str(image_path.relative_to(scan_root))
        signature = file_signature(image_path)
        cached = files_cache.get(rel)
        if cached and cached.get("signature") == signature:
            vectors = cached.get("encodings", [])
        else:
            vectors = [vector.tolist() for vector in backend.encode(image_path)]
            files_cache[rel] = {"signature": signature, "encodings": vectors}

        for index, vector in enumerate(vectors):
            records.append(
                FaceRecord(
                    file_path=image_path,
                    face_index=index,
                    encoding=np.asarray(vector, dtype=float),
                )
            )
    return records


def pairwise_distances(records: list[FaceRecord]) -> np.ndarray:
    matrix = np.vstack([item.encoding for item in records])
    diff = matrix[:, None, :] - matrix[None, :, :]
    return np.linalg.norm(diff, axis=2)


def connected_components(adjacency: np.ndarray) -> list[list[int]]:
    count = adjacency.shape[0]
    visited = np.zeros(count, dtype=bool)
    components: list[list[int]] = []

    for start in range(count):
        if visited[start]:
            continue
        stack = [start]
        visited[start] = True
        component: list[int] = []
        while stack:
            node = stack.pop()
            component.append(node)
            neighbors = np.where(adjacency[node])[0]
            for neighbor in neighbors:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    stack.append(int(neighbor))
        components.append(component)
    return components


def cluster_with_eps(records: list[FaceRecord], eps: float, min_samples: int) -> ClusterResult:
    if not records:
        return ClusterResult(clusters=[], noise=[], eps=eps)
    distances = pairwise_distances(records)
    adjacency = distances <= eps
    components = connected_components(adjacency)
    clusters: list[list[FaceRecord]] = []
    noise: list[FaceRecord] = []
    for component in components:
        members = [records[index] for index in component]
        if len(members) >= min_samples:
            clusters.append(sorted(members, key=lambda item: (str(item.file_path), item.face_index)))
        else:
            noise.extend(members)
    clusters.sort(key=lambda items: (-(len(items)), str(items[0].file_path)))
    return ClusterResult(clusters=clusters, noise=sorted(noise, key=lambda item: (str(item.file_path), item.face_index)), eps=eps)


def candidate_eps_values(records: list[FaceRecord]) -> list[float]:
    if len(records) < 2:
        return [DEFAULT_EPS]
    distances = pairwise_distances(records)
    triu = distances[np.triu_indices(len(records), k=1)]
    unique = sorted(set(float(item) for item in triu))
    if not unique:
        return [DEFAULT_EPS]
    candidates = [0.0]
    for index in range(len(unique) - 1):
        candidates.append((unique[index] + unique[index + 1]) / 2.0)
    candidates.append(unique[-1] + 1e-9)
    candidates.append(DEFAULT_EPS)
    return sorted(set(candidates))


def auto_cluster(records: list[FaceRecord], min_samples: int, cluster_count: int | None) -> ClusterResult:
    if cluster_count is None:
        return cluster_with_eps(records, DEFAULT_EPS, min_samples)
    candidates = candidate_eps_values(records)
    best: ClusterResult | None = None
    best_score: tuple[float, int] | None = None
    for eps in candidates:
        result = cluster_with_eps(records, eps, min_samples)
        diff = abs(len(result.clusters) - cluster_count)
        score = (float(diff), -sum(len(cluster) for cluster in result.clusters))
        if best_score is None or score < best_score:
            best = result
            best_score = score
    return best if best is not None else cluster_with_eps(records, DEFAULT_EPS, min_samples)


def build_catalog(scan_root: Path, result: ClusterResult) -> dict[str, Any]:
    clusters: list[dict[str, Any]] = []
    for index, members in enumerate(result.clusters, start=1):
        clusters.append(
            {
                "cluster_id": f"person_{index:03d}",
                "size": len(members),
                "files": [
                    {
                        "path": str(member.file_path.relative_to(scan_root)),
                        "face_index": member.face_index,
                    }
                    for member in members
                ],
            }
        )
    return {
        "scan_root": str(scan_root),
        "eps": result.eps,
        "clusters": clusters,
        "noise": [
            {"path": str(member.file_path.relative_to(scan_root)), "face_index": member.face_index}
            for member in result.noise
        ],
    }


def safe_link_name(scan_root: Path, target: Path, face_index: int) -> str:
    rel = str(target.relative_to(scan_root))
    sanitized = rel.replace(os.sep, "__")
    return f"{sanitized}__face{face_index}{target.suffix.lower()}"


def export_folders(scan_root: Path, result: ClusterResult, export_dir: Path) -> None:
    export_dir.mkdir(parents=True, exist_ok=True)
    for index, members in enumerate(result.clusters, start=1):
        person_dir = export_dir / f"person_{index:03d}"
        person_dir.mkdir(parents=True, exist_ok=True)
        for member in members:
            link_name = safe_link_name(scan_root, member.file_path, member.face_index)
            link_path = person_dir / link_name
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            link_path.symlink_to(member.file_path.resolve())


def run(
    *,
    scan_path: Path,
    cluster_count: int | None,
    min_samples: int,
    export_json: bool,
    export_folders_flag: bool,
    backend_name: str,
    catalog_path: Path,
    cache_path: Path,
    folders_dir: Path,
) -> dict[str, Any]:
    backend = pick_backend(backend_name)
    images = discover_images(scan_path)
    cache_payload = load_cache(cache_path)
    records = extract_records(images, backend=backend, cache_payload=cache_payload, scan_root=scan_path)
    result = auto_cluster(records, min_samples=min_samples, cluster_count=cluster_count)
    catalog = build_catalog(scan_path, result)

    save_cache(cache_path, cache_payload)
    if export_json:
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    if export_folders_flag:
        export_folders(scan_path, result, folders_dir)
    return catalog


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scan_path = Path(args.scan).resolve()
    if not scan_path.exists() or not scan_path.is_dir():
        print(f"Error: scan path does not exist or is not a directory: {scan_path}", file=sys.stderr)
        return 1

    catalog_path = Path(args.catalog_path).resolve() if args.catalog_path else scan_path / CATALOG_FILENAME
    cache_path = Path(args.cache_path).resolve() if args.cache_path else scan_path / CACHE_FILENAME
    folders_dir = Path(args.folders_dir).resolve() if args.folders_dir else scan_path / FOLDERS_DIRNAME

    try:
        catalog = run(
            scan_path=scan_path,
            cluster_count=args.cluster_count,
            min_samples=args.min_samples,
            export_json=args.export_json,
            export_folders_flag=args.export_folders,
            backend_name=args.backend,
            catalog_path=catalog_path,
            cache_path=cache_path,
            folders_dir=folders_dir,
        )
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Processed clusters={len(catalog['clusters'])}, "
        f"noise={len(catalog['noise'])}, "
        f"catalog={catalog_path if args.export_json else 'not exported'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
