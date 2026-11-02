# -*- coding: utf-8 -*-
"""Split oversized sections of the nsfw-h3-video-pipeline SKILL.md into references/.

The skill file grew past the 100k character limit that skill_manage enforces, which
blocks every future skill_manage patch on it. The fix is to move self-contained
reference sections into their own files under references/ and leave a pointer.

Sections are moved verbatim; the pointer keeps the original heading so a search for
the heading still finds it.
"""
import os
import re
import sys

SKILL_DIR = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "hermes", "skills", "nsfw-h3-video-pipeline"
)
SKILL = os.path.join(SKILL_DIR, "SKILL.md")
REFS = os.path.join(SKILL_DIR, "references")

# heading prefix -> reference file name
MOVE = {
    "## Tested Pipelines": "tested-pipelines.md",
    "## Workflow Rules": "workflow-rules.md",
    "## Discover newly discovered Qwen-Image 2.1": "qwen-image-2.1-discovery.md",
    "## Qwen-Image-Edit: снятие одежды": "qwen-image-edit-undress-skin.md",
    "## Reusable utilities under": "hermes-scripts-utilities.md",
    "## LoRA Inventory": "lora-inventory.md",
    "## H3 does NOT do video-to-video": "wan22-vace-video-to-video.md",
    "## LoRA compatibility for H3": "h3-lora-compatibility.md",
}


def split_sections(text):
    parts = re.split(r"(?m)^(?=## )", text)
    return parts


def main():
    text = open(SKILL, encoding="utf-8", errors="replace").read()
    parts = split_sections(text)
    moved = []
    kept = []
    for pt in parts:
        if not pt.startswith("## "):
            kept.append(pt)
            continue
        head = pt.split("\n", 1)[0]
        target = None
        for prefix, fname in MOVE.items():
            if head.startswith(prefix):
                target = fname
                break
        if target is None:
            kept.append(pt)
            continue
        body = pt.rstrip() + "\n"
        path = os.path.join(REFS, target)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        moved.append((head, target, len(body)))
        kept.append(
            f"{head}\n\n"
            f"Полный раздел вынесен в `references/{target}` "
            f"(не редактировать здесь - править в файле-источнике).\n"
        )

    new_text = "".join(kept)
    # добавить новые файлы в список references во фронтматтере
    fm_end = new_text.find("\n---\n", 4)
    fm = new_text[:fm_end]
    body = new_text[fm_end:]
    lines = []
    for _, fname, _ in moved:
        entry = f"  - references/{fname}"
        if entry not in fm:
            lines.append(entry)
    if lines:
        fm = fm.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    new_text = fm + body
    open(SKILL, "w", encoding="utf-8").write(new_text)

    print(f"SKILL.md: {len(text)} -> {len(new_text)} символов")
    for head, fname, size in moved:
        print(f"  вынесено {size:6d}  {fname}   <- {head[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
