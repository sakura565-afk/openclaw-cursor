# Аудит локальных возможностей ComfyUI под задачу «замена ткани на мебели»
# Дата: 24.11.2026 | M1 = RTX 4070 Ti SUPER, 16 ГБ | ComfyUI 0.45.0

## Что УЖЕ установлено и работает
| Компонент | Размер | Роль |
|---|---|---|
| qwen_image_2.1_int8_convrot | 7.3 ГБ | редактирование, 16 ГБ VRAM хватает |
| qwen3vl_8b_int8_convrot | 9.4 ГБ | text encoder для Qwen-Image |
| qwen_image_2.1_vae_bf16 | 675 МБ | VAE |
| qwen_image_inpaint_diffsynth_controlnet | 2.3 ГБ | ControlNet для inpaint |
| flux1-fill-dev-Q8_0.gguf | 12.7 ГБ | Flux Fill (генеративный, хуже детерминированного) |
| z_image_turbo_fp8 + z_image_turbo_te_fp8 | - | быстрый T2I |

## Ноды ComfyUI 0.45.0, релевантные задаче
- TextEncodeQwenImage21 (images=) - Qwen-Image 2.1, референсы через images
- TextEncodeQwenImageEdit / TextEncodeQwenImageEditPlus (image=) - Qwen-Image-Edit
- VAEEncodeForInpaint (pixels, vae, mask, grow_mask_by) - маскированный inpaint
- QwenImageDiffsynthControlnet - управление структурой при inpaint
- Flux2ProImageNode / Flux2MaxImageNode (images=) - FLUX.2 с референсами
- ImageCompositeMasked - возврат защищённых областей из оригинала
- PatinaMaterialNode / PatinaMaterialExtractNode / PatinaPBRMapsNode -
  генерация и ИЗВЛЕЧЕНИЕ PBR-карт (basecolor/normal/roughness/height/metalness),
  tiling_mode, mask. Это база гибридного режима «точный артикул».
- ComfyUI-Inpaint-CropAndStitch, Impact-Pack, KJNodes - вспомогательное

## Чего НЕТ (нужно поставить)
- Шаблоны workflow вообще не установлены (0 json в workflow_templates) -
  официальный Material Replacement придётся собирать или качать JSON с comfy.org
- Qwen-Image-Edit-2511 (53.8 ГБ fp32; есть GGUF-варианты) - НЕ установлен
- FLUX.2 Klein (4B - 22 ГБ fp32 / fp8 ~7 ГБ; 9B - 49 ГБ fp32) - НЕ установлен
- Qwen-Image-Layered (53.8 ГБ) - НЕ установлен
- Patina - ноды ЕСТЬ, но partner-пакет, требует проверки лицензии/активации

## Проверено на HF (актуальные размеры)
- Qwen/Qwen-Image-Edit-2511: transformer 5x9.3 ГБ + text_encoder 4x ~4.6 ГБ = 53.8 ГБ fp32
- black-forest-labs/FLUX.2-klein-4B: 7.2 ГБ fp8-веса + qwen3-8b энкодер
- Comfy-Org/Flux-2-Klein-4B / 9B: готовые single-file для ComfyUI
  (9B: qwen_3_8b_fp8mixed 8.07 ГБ + flux2-vae 0.31 ГБ)

## Вывод по приоритету
1. Qwen-Image-2.1 int8 - уже стоит, влезает в 16 ГБ, поддерживает референсы.
   Первым делом - masked inpaint (VAEEncodeForInpaint + Diffsynth ControlNet).
2. Klein 4B - роль быстрого перебора тканей (дистилляция, 4 шага).
3. 2511 Material Replacement - когда появится шаблон; 53.8 ГБ fp32,
   для 16 ГБ нужен GGUF/FP8.
4. Точный артикул - Patina PBR: извлечь albedo обивки из фото, подставить
   цвет артикула в albedo, сохранить normal/roughness -> свет и фактура
   восстанавливаются, рисунок и раппорт задаются явно.
