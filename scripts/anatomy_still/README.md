# Анатомический still-референс для H3

Конвейер делает Qwen-Edit с малой силой изменения, строит маску таза по позе и
повторяет локальный masked refine до прохождения численного валидатора. H3 затем
получает готовый still как `first_frame`; описание анатомии в H3-промт не нужно.

Все параметры в `recipes.json` имеют статус `dry_run_unverified`, пока соответствующая
сцена не прошла реальный GPU-прогон. Dry-run не обращается к ComfyUI.

```bash
python -m scripts.anatomy_still.cli mask --image still.jpg --mask-out pelvis_mask.png
python -m scripts.anatomy_still.cli validate --image still.jpg --mask pelvis_mask.png
python -m scripts.anatomy_still.cli run --scene chef --source chef_src.jpg --dry-run
python scripts/h3_i2v_run.py --still-reference still.png --recipe-prompt chef ...
```

Автоматическая маска использует MediaPipe Pose на CPU. Пакет не входит в базовые
зависимости CI: установите `mediapipe==0.10.21` только для подготовки маски. Либо
передайте ранее автоматически построенную маску валидатору напрямую.

Готовый проверенный пример — `examples/chef3_final.png` и его автоматическая маска
`examples/chef3_final_mask.png`: visibility **0.499409**, cloth **0.437934**, PASS.
Это существующий кадр H3, не новый Qwen-рендер. Числа по всем трём сценам и честный
статус параметров — в `REPORT.md` и `report.json`.

```bash
python -m scripts.anatomy_still.cli validate \
  --image scripts/anatomy_still/examples/chef3_final.png \
  --mask scripts/anatomy_still/examples/chef3_final_mask.png
python scripts/h3_i2v_run.py \
  --still-reference scripts/anatomy_still/examples/chef3_final.png \
  --recipe-prompt chef --prompt-file prompt.txt --prefix chef_still
```
