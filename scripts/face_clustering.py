#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

import numpy as np
from PIL import Image


DEFAULT_EPS = 0.6
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
        from insightface.app import FaceAnalysis  # type: ignore

        self._app = FaceAnalysis(name="buffalo_l")
        self._app.prepare(ctx_id=-1)

    def encode(self, image_path: Path) -> list[np.ndarray]:
        rgb_image = np.array(Image.open(image_path).convert("RGB"))
        bgr_image = rgb_image[:, :, ::-1]
        faces = self._app.get(bgr_image)
        return [np.asarray(face.embedding, dtype=float) for face in faces]


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
        choices=("auto", "face_recognition", "insightface"),
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

    errors: list[str] = []
    try:
        return FaceRecognitionBackend()
    except Exception as exc:  # pragma: no cover - depends on optional library presence
        errors.append(f"face_recognition: {exc}")
    try:
        return InsightFaceBackend()
    except Exception as exc:  # pragma: no cover - depends on optional library presence
        errors.append(f"insightface: {exc}")
    raise RuntimeError("No available face backend. Install face_recognition or insightface. " + " | ".join(errors))


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
