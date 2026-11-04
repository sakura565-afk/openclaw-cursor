"""Blind A/B judging for AI video skin-detail candidates.

Why this package exists
-----------------------
The old way of judging a detail pass was two things at once, and both of them
are unreliable:

* a "pore energy" number that counts all high-frequency energy, so an
  isotropic noise field reads as +77% texture and a human looking at the crop
  calls it "digital sand";
* a human looking at ONE crop, which gave the same crop 5/10 and 4/10 on two
  passes, and the pore crop 3/10 and 1/10.

So the verdict has to be built out of parts that cannot be fooled by either
failure mode:

* the blind contact sheet removes the knowledge of which variant is which
  (``--seed`` fixes the shuffle, and the labels carry no file names);
* the crops are PAIRED - the same skin region, in the same frame position, is
  cut from every variant - so a judge compares like with like;
* the objective metrics are reported with an explicit comparability verdict,
  so a "flicker" number is never printed for clips that are not the same take;
* the human/vision side of the judgement is external by construction: this
  package prepares the artefacts and prints a ready-made prompt, and the
  scores come back through ``--scores``.

Nothing here touches a GPU or ComfyUI; it is CPU-only frame and maths work.
"""

from __future__ import annotations

__all__ = ["judge"]

__version__ = "1.0.0"
