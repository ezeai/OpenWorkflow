"""Face similarity for a character: ArcFace embeddings from insightface, cosine similarity.

Standalone on purpose: it runs under ComfyUI's own Python (which has insightface), not under Open Workflow's.
Usage: python -s face_score.py <insightface models folder> <reference image> <candidate image>
Prints one line: "similarity <number>", or "noface <which>" when no face is found in an image.
A sheet can hold several faces: the reference face is compared with every face in the candidate, and the best match is reported.
"""
import sys

import numpy as np
from PIL import Image
from insightface.app import FaceAnalysis


def _load(path: str) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"))[:, :, ::-1]


def main(models_root: str, reference: str, candidate: str) -> None:
    app = FaceAnalysis(name="antelopev2", root=models_root, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    def faces(path: str):
        return [f for f in app.get(_load(path))]

    ref_faces = faces(reference)
    if not ref_faces:
        print("noface reference")
        return
    # the reference is the largest face in the anchor photo
    ref = max(ref_faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])).normed_embedding
    cand_faces = [f.normed_embedding for f in faces(candidate)]
    if not cand_faces:
        print("noface candidate")
        return
    best = max(float(np.dot(ref, e)) for e in cand_faces)
    print(f"similarity {best:.4f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
