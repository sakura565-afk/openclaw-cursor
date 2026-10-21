"""Проверенные константы MiniMax H3 I2V (fl2va) на домашнем стеке.

Источник всех чисел — реальные рендеры на хосте:

* ComfyUI ``ComfyUI_windows_portable`` на порту **8188** (не 8181 — частая ошибка
  в чужих конспектах, запрос на 8181 уходит в никуда и агент ждёт таймаут);
* GPU RTX 3060 12 GB, база ``x_h3_nvfp4_awq.safetensors``;
* каталог LoRA ``E:\\AI\\models\\comfyui\\loras\\``.

Правило модуля: каждая константа снабжена комментарием-источником. Если число
не проверено рендером — оно сюда не попадает, иначе агент снова начнёт
подбирать параметры наугад и повторит ту же серию брака.

Только stdlib.
"""

from __future__ import annotations

from typing import Any, NamedTuple

# --------------------------------------------------------------------------- #
# Окружение
# --------------------------------------------------------------------------- #

# Порт ComfyUI на хосте. Проверено: 8188 отвечает на /prompt, 8181 — нет.
COMFYUI_PORT = 8188
COMFYUI_URL = f"http://127.0.0.1:{COMFYUI_PORT}"

# База H3, под которую подобраны все остальные константы.
BASE_MODEL = "x_h3_nvfp4_awq.safetensors"

# Каталоги LoRA на хосте. Их ДВА, и они разные — проверено на диске.
# ComfyUI принимает имя файла, а не путь, поэтому искать надо по всем каталогам.
#
#   E:/AI/models/comfyui/loras/
#       NSGIRL-MiniMax-H3-LoRA-By-MM744.safetensors   (анатомия)
#       h3-realism-people-t2v-i2v-r2v.safetensors      (реализм тела/кожи)
#       + Flux/SDXL-шум: NSFW_master, nsfw_flux_lora_v1, flux2klein_nsfw,
#         undressing-PN, aidmaNSFWunlock-FLUX-V0.2, epiCRealismXL-KiSSEnhancer_Lora,
#         faceid.plusv2.sdxl.lora
#   C:/Users/user/comfyui/ComfyUI_windows_portable/ComfyUI/models/loras/
#       minimax-h3-facial-realism-closeup-cp2000.safetensors  (реализм лица)
#       minimax_h3_turbo_4step_ema_pruned.safetensors
#       faceid.plusv2.sdxl.lora.safetensors
LORA_DIRS: tuple[str, ...] = (
    r"E:\AI\models\comfyui\loras",
    r"C:\Users\user\comfyui\ComfyUI_windows_portable\ComfyUI\models\loras",
)

# Оставлено для обратной совместимости: первый каталог как "основной".
LORA_DIR = LORA_DIRS[0]

# H3 всегда отдаёт 24 fps; длительность клипа = length / FPS.
FPS = 24

# Итоговые файлы после RIFE-интерполяции — 60 fps.
# RIFE: minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1
RIFE_FPS = 60

# --------------------------------------------------------------------------- #
# Длина клипа
# --------------------------------------------------------------------------- #

# Длины в кадрах, которые ComfyUI реально принимает для H3 I2V на этом стеке
# (проверено рендерами: всё, что вне сетки, узел отклоняет либо выдаёт битый
# хвост). 175 кадров / 24 fps = 7.29 с — максимум, который стабильно
# докручивается на 12 GB VRAM.
VALID_LENGTHS: tuple[int, ...] = (80, 113, 145, 175)

# Длительность каждой валидной длины в секундах — для сообщений валидатора.
LENGTH_SECONDS: dict[int, float] = {length: round(length / FPS, 2) for length in VALID_LENGTHS}

# --------------------------------------------------------------------------- #
# Сетка разрешений
# --------------------------------------------------------------------------- #


class Resolution(NamedTuple):
    """Разрешение из проверенной сетки.

    ``aspect_ratio`` — фактический AR (width / height), а не «паспортный»:
    именно с ним надо сравнивать AR исходника, иначе кадр поедет кропом.
    """

    width: int
    height: int
    aspect_ratio: float
    note: str


def _res(width: int, height: int, note: str) -> Resolution:
    """Собрать запись сетки, посчитав фактический AR."""
    return Resolution(width=width, height=height, aspect_ratio=width / height, note=note)


# Сетка, проверенная на хосте. Фактический AR указан в комментарии к строке —
# он же считается в ``_res``. Другие размеры на этом стеке не проверялись:
# H3 их принимает, но качество/VRAM непредсказуемы.
RESOLUTIONS: tuple[Resolution, ...] = (
    # AR 0.6667 — вертикаль 2:3, рабочая лошадка для портретных исходников.
    _res(768, 1152, "Портрет 2:3, базовый режим для вертикальных исходников"),
    # AR 0.7000 — ровный AR, лучший компромисс детализации и VRAM.
    _res(896, 1280, "Портрет 7:10, максимум детализации, который держит 12 GB"),
    # AR 0.8421 — почти 5:6, под слегка «квадратные» исходники.
    _res(1024, 1216, "Почти квадрат 5:6, под широкие портретные исходники"),
    # AR 0.7027 — близко к 896x1280, но дешевле по VRAM.
    _res(832, 1184, "Портрет ~7:10, облегчённый вариант 896x1280"),
    # AR 0.6250 — 5:8, самый дешёвый режим для черновых прогонов.
    _res(640, 1024, "Портрет 5:8, черновые прогоны и быстрые итерации"),
)

# Допустимое расхождение AR исходника и AR выходного разрешения. Проверено:
# при расхождении больше 0.005 H3 начинает незаметно кропать/растягивать кадр,
# и склейка клипов перестаёт совпадать по композиции.
AR_TOLERANCE = 0.005

# --------------------------------------------------------------------------- #
# Сэмплер и шифты
# --------------------------------------------------------------------------- #

# Единственный сэмплер, который корректно тянет совместный video+audio сэмплинг H3.
DEFAULT_SAMPLER = "res_multistep"

# 6 шагов — рабочая точка на этом стеке: больше шагов не улучшают картинку
# заметно, но линейно съедают время рендера.
DEFAULT_STEPS = 6

# Шифты по умолчанию, с которыми снята большая часть удачных рендеров.
DEFAULT_SHIFT_VIDEO = 12.0
DEFAULT_SHIFT_AUDIO = 3.0

# Полосы значений, в которых шифты проверены рендерами (вне полосы — предупреждение).
# shift_video 8-10 даёт более фотореалистичную кожу, но теряет мелкую анатомию
# (см. pitfalls.md, пункт «кожа восковая»).
SHIFT_VIDEO_VERIFIED_RANGE = (8.0, 14.0)
SHIFT_AUDIO_VERIFIED_RANGE = (2.0, 5.0)

# Жёсткие границы: за ними H3 разваливает движение/звук, это ошибка, а не вкусовщина.
SHIFT_VIDEO_HARD_RANGE = (1.0, 30.0)
SHIFT_AUDIO_HARD_RANGE = (0.5, 15.0)

# --------------------------------------------------------------------------- #
# LoRA
# --------------------------------------------------------------------------- #

# Признак H3-совместимой LoRA: ключи тензоров начинаются именно так.
# Всё остальное (Flux/SDXL-раскладки) H3 либо игнорирует, либо ломает.
H3_COMPATIBLE_LORA_KEY_PREFIX = "diffusion_model.blocks."

# Вторая H3-раскладка — короткая, без префикса "diffusion_model.".
# Проверено на хосте: minimax-h3-facial-realism-closeup-cp2000.safetensors
# (base_model в метаданных: MiniMax/MiniMax-H3, 200 тензоров).
H3_SHORT_KEY_PREFIX = "blocks."

# LoRA, проверенные на хосте.
#
# Формат записи: strength_model / strength_clip — рекомендуемые силы,
# trigger — слово, без которого LoRA не включается, purpose — зачем она нужна.
# ``verified_filename`` отмечает, что имя файла подтверждено на хосте.
KNOWN_LORAS: dict[str, dict[str, Any]] = {
    "NSGIRL-MiniMax-H3-LoRA-By-MM744.safetensors": {
        # Проверенные боевые значения (run_h3_panties_off_v7_nsgirl.py):
        #   strength_model 0.9, strength_clip 0.5
        "strength_model": 0.9,
        "strength_clip": 0.5,
        "trigger": "nsgirl",
        "purpose": (
            "Генитальная анатомия (labia majora/minora, отверстие). "
            "Без неё H3 рисует в этой зоне гладкую кожу без анатомии."
        ),
        # Проверено: 0.85-0.9 — рабочая полка. Понижение силы не лечит восковую
        # кожу (прогон 0.9 -> 0.65 не дал улучшения), так что уходить вниз незачем.
        "strength_range": (0.85, 0.9),
        "avoid_with": ("h3-realism-people-t2v-i2v-r2v.safetensors",),
        "verified_filename": True,
        "notes": (
            "На силе 1.0 вместе с shift_video 8-10 даёт фотореалистичную кожу, "
            "но теряет labia minora — выбирать по приоритету брифа."
        ),
    },
    "h3-realism-people-t2v-i2v-r2v.safetensors": {
        # Проверено в run_h3_butt_pushin_v1.py: strength_model 0.7
        "strength_model": 0.7,
        "strength_clip": 0.6,
        "trigger": None,
        "purpose": "Фотореализм кожи для людей (t2v/i2v/r2v).",
        "strength_range": (0.5, 0.7),
        "avoid_with": ("NSGIRL-MiniMax-H3-LoRA-By-MM744.safetensors",),
        "verified_filename": True,
        # Проверено рендерами: в паре с NSGIRL ухудшает анатомию, а восковую кожу
        # при этом не чинит. Оставлена в каталоге, чтобы агент не «открывал» её заново.
        "notes": "НЕ использовать вместе с NSGIRL: ломает анатомию и не чинит кожу.",
    },
    "minimax-h3-facial-realism-closeup-cp2000.safetensors": {
        # Типичная сила 0.45: выше — лицо «пластилинится» и уезжает от исходника.
        "strength_model": 0.45,
        "strength_clip": 0.35,
        "trigger": None,
        "purpose": "Фотореализм лица (facial realism), мелкая кожная текстура лица.",
        "strength_range": (0.4, 0.5),
        "avoid_with": (),
        # Имя сверено с диском хоста:
        #   ComfyUI/models/loras/minimax-h3-facial-realism-closeup-cp2000.safetensors
        # и с рабочими скриптами, которые реально рендерили:
        #   F:/AI/comfyui-qwen/run_h3_panties_off_v7_nsgirl.py
        #   F:/AI/comfyui-qwen/run_h3_panties_off_v6b.py
        #   F:/AI/comfyui-qwen/run_h3_legs_open_v1.py
        #   F:/AI/comfyui-qwen/run_h3_pose_slow_v1.py
        "verified_filename": True,
        "notes": "Сила 0.45 — проверенная рабочая точка, выше 0.5 лицо уходит от исходника.",
    },
}

# LoRA, найденные в каталоге хоста и проверенные как H3-НЕсовместимые.
# Перечислены явно, чтобы агент не тратил прогоны на «переберём все NSFW подряд».
KNOWN_INCOMPATIBLE_LORAS: tuple[str, ...] = (
    "NSFW_master",
    "nsfw_flux_lora_v1",
    "flux2klein_nsfw",
    "undressing-PN",
)

# --------------------------------------------------------------------------- #
# Промты и камера
# --------------------------------------------------------------------------- #

# Маркеры наезда камеры в позитивном промте.
#
# ВАЖНО: маркер "camera" здесь НЕ используется — он ловит "gaze stays at the
# camera" (взгляд в объектив), а это не движение камеры. На реальных рабочих
# скриптах (run_h3_panties_off_v7_nsgirl.py) такой маркер давал ложное
# предупреждение о конфликте.
CAMERA_PUSH_IN_MARKERS: tuple[str, ...] = (
    "zoom in",
    "zooming in",
    "push in",
    "push-in",
    "pushes in",
    "dolly in",
    "camera moves closer",
    "camera moves in closer",
    "closer to her",
    "close-up of",
    "closeup of",
    "camera pushes",
    "camera closes in",
)

# Термины в негативе, которые убивают наезд камеры: стандартный набор
# «camera pan, camera zoom» запрещает ровно то движение, которое просит бриф.
CAMERA_NEGATIVE_CONFLICT_TERMS: tuple[str, ...] = (
    "camera zoom",
    "zoom in",
    "camera pan",
    "camera movement",
)

# Вместо запрета зума надо запрещать обратное движение.
RECOMMENDED_CAMERA_NEGATIVE = (
    "camera pull back, zoom out, subject shrinking in frame, retreating camera"
)


# --------------------------------------------------------------------------- #
# Хелперы
# --------------------------------------------------------------------------- #


def is_valid_length(length: Any) -> bool:
    """Проверить, что ``length`` лежит в сетке допустимых длин H3."""
    return isinstance(length, int) and not isinstance(length, bool) and length in VALID_LENGTHS


def nearest_valid_length(length: int) -> int:
    """Вернуть ближайшую валидную длину (при равенстве — меньшую, она дешевле)."""
    return min(VALID_LENGTHS, key=lambda valid: (abs(valid - length), valid))


def length_to_seconds(length: int) -> float:
    """Перевести длину в кадрах в секунды при 24 fps."""
    return round(length / FPS, 2)


def aspect_ratio(width: int, height: int) -> float:
    """Посчитать AR как width / height."""
    if height <= 0:
        raise ValueError(f"height must be positive, got {height}")
    return width / height


def find_resolution(width: int, height: int) -> Resolution | None:
    """Найти разрешение в проверенной сетке, иначе ``None``."""
    for resolution in RESOLUTIONS:
        if resolution.width == width and resolution.height == height:
            return resolution
    return None


def resolutions_for_ar(source_ar: float, tolerance: float = AR_TOLERANCE) -> list[Resolution]:
    """Вернуть разрешения сетки, чей AR совпадает с исходником в пределах допуска."""
    return [res for res in RESOLUTIONS if abs(res.aspect_ratio - source_ar) <= tolerance]


def format_resolution_grid() -> str:
    """Отрендерить сетку разрешений одной строкой для сообщений валидатора."""
    return ", ".join(f"{res.width}x{res.height} (AR {res.aspect_ratio:.4f})" for res in RESOLUTIONS)
