# Аудит ветки «сложные текстуры и раппорт ткани» (2.5D)
# 24.11.2026 | ComfyUI 0.45.0 | M1 = RTX 4070 Ti SUPER 16 ГБ

## Итог в одну строку
Схема верная по инженерной логике, но предложенные в тексте пакеты
(LayerStyle, Majoor-ImageOps) на машине НЕ установлены, а встроенной
перспективы в ComfyUI 0.45 нет. При этом встроен 3D-пайплайн
(MoGe -> UnwrapMesh -> ApplyTextureToMesh -> RenderMesh), который для
сохранения ФАЗЫ и МАСШТАБА раппорта точнее 2.5D corner pin.

## Чего НЕТ (из текста)
| Пакет | Статус | Данные |
|---|---|---|
| ComfyUI_LayerStyle | НЕ установлен | 3217 stars, push 2026-11-07, живой |
| ComfyUI-Majoor-ImageOps | НЕ установлен | 14 stars, push 2026-09-30, живой |
| ImageOpsCornerPin | нет в object_info | есть в реестре менеджера -> ставится |
| ImageOpsDistort | нет в object_info | есть в реестре менеджера -> ставится |
| ImageShift / ImageBlendAdvance / DistortDisplace | нет | только из LayerStyle |

Оба репо в реестре comfyui-manager (custom-node-list.json) -> установка
через cm-cli возможна.

## Что ЕСТЬ встроенным (ComfyUI 0.45.0)
### 3D-ветка (comfy_extras/mesh3d/, nodes_moge.py)
- MoGeInference / LoadMoGeModel / MoGePointMapToMesh - геометрия из фото
- UnwrapMesh(mesh, segmenter, resolution, padding) - UV-развёртка
- ApplyTextureToMesh(mesh, base_color) - наложение текстуры по UV
- MeshTextureToImage / RenderMesh(mesh, mode, camera_info, texture)
- RenderMesh принимает camera_info -> рендер с ИСХОДНОЙ камеры
- LoadMoGeModel: список моделей ПУСТ -> нужна Comfy-Org/MoGe (193k скачиваний)

### Прочее
- ImageBlend (comfy_extras/nodes_post_processing.py): image1,image2,
  blend_factor, blend_mode[normal|multiply|screen|overlay|soft_light|difference]
- PatinaMaterialExtractNode - извлечение PBR-карт ИЗ ФОТО (basecolor/normal/
  roughness/height), есть mask, tiling_mode[both|horizontal|vertical]
- PatinaPBRMapsNode - PBR-карты из фото/render
- ImageCompositeMasked - возврат защищённых областей
- ComfyUI_IPAdapter_plus - перенос стиля/текстуры (unified loader)
- ComfyUI-Inpaint-CropAndStitch, Impact-Pack, KJNodes - вспомогательное
- opencv 4.11.0 + numpy 2.5.4 в python_embeded -> свой corner pin на Python
  без внешнего пакета (getPerspectiveTransform + warpPerspective)

## Отсутствующее критично
- PerspectiveWarpTransform во встроенных НЕТ (grep по всему ComfyUI)
- Встроенного ImageShift (сдвиг бесшовного повтора) НЕТ
- Встроенного node для построения бесшовного полотна (tile) НЕТ
  -> собирать полотно в Python (numpy.tile) либо Patina (tiling_mode)

## Мой вывод по архитектуре
1. Для ТОЧНОГО раппорта: 3D-ветка сильнее 2.5D. UnwrapMesh даёт UV,
   ApplyTextureToMesh натягивает ткань по геометрии, RenderMesh рендерит
   с исходной камеры -> фаза и масштаб сохраняются автоматически.
2. Для ПРИБЛИЖЁННО плоских деталей 2.5D corner pin достаточен и дешевле.
3. Светотень: НЕ подавать исходное фото в деформацию (яркость != геометрия).
   Правильно: Patina PBR-карты -> albedo для цвета, normal/roughness для
   фактуры, отдельная карта светотени для умножения.
4. Фаза (offset_u/offset_v) задаётся на панель, НЕ автоматически.
5. Приёмочный фильтр - по ПИКСЕЛЯМ ТКАНИ, не по маске (урок из FURN-OWN).
