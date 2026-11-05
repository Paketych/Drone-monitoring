"""Обучение детектора агента готовности (docs/archive/BACKEND_PLAN.md, разделы 3.2 и 3.7).

    python ml/train.py                                # YOLO26m, imgsz 1280, 150 эпох, val из val_objects.txt
    python ml/train.py --model yolo11m.pt             # запасной вариант
    python ml/train.py --batch 8                      # на видеокарте от 12 ГБ
    python ml/train.py --epochs 1 --name smoke        # проверить, что всё запускается
    python ml/train.py --export onnx                  # после обучения выгрузить ONNX

Перед обучением датасет пересобирается из ml/annotations/ (prepare_dataset.py).
Лучшие веса копируются в weights/readiness_<модель>.pt, метрики val — в runs/<name>/metrics_val.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import torch
from common import REPO, load_val_objects
from prepare_dataset import _object_key, build, parse_objects
from ultralytics import YOLO

WEIGHTS_DIR = REPO / "weights"
RUNS_DIR = REPO / "runs"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--model", default="yolo26m.pt", help="предобученные веса или .yaml архитектуры")
    p.add_argument(
        "--imgsz", type=int, default=1280, help="1280: на меньшем размере теряются окна и top_high"
    )
    p.add_argument("--epochs", type=int, default=150)
    p.add_argument(
        "--patience", type=int, default=50, help="остановка, если mAP на val не растёт столько эпох"
    )
    p.add_argument(
        "--batch",
        type=int,
        default=2,
        help="2 занимает ~5 ГБ при imgsz 1280 (YOLO26m); на 6 ГБ batch 4 вылезает в общую память и в 4 раза медленнее",
    )
    p.add_argument(
        "--nbs",
        type=int,
        default=64,
        help="номинальный batch: веса обновляются раз в nbs/batch шагов; при batch 2 и nbs 64 это редко",
    )
    p.add_argument("--device", default="0" if torch.cuda.is_available() else "cpu")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument(
        "--cache",
        default="ram",
        choices=["ram", "disk", "none"],
        help="кэш декодированных снимков: исходники 5280x3956 читаются медленно",
    )
    p.add_argument("--val-objects", default=load_val_objects())
    p.add_argument("--exclude-objects", default="")
    p.add_argument(
        "--include-prelabel", action="store_true", help="учиться и на непроверенных рамках модели"
    )
    p.add_argument("--name", default="readiness", help="папка запуска в runs/")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--export", choices=["onnx", "openvino"], help="формат экспорта лучших весов")
    return p.parse_args()


def resolve_model(model: str) -> str:
    """Голое имя весов скачивается в weights/, а не в текущую папку."""
    p = Path(model)
    if p.suffix == ".pt" and p.parent == Path(".") and not p.exists():
        WEIGHTS_DIR.mkdir(exist_ok=True)
        return str(WEIGHTS_DIR / p.name)
    return model


def main() -> None:
    args = parse_args()
    data_yaml = build(
        parse_objects(args.val_objects),
        parse_objects(args.exclude_objects),
        include_prelabel=args.include_prelabel,
    )

    model = YOLO(resolve_model(args.model))
    model.train(
        data=str(data_yaml),
        imgsz=args.imgsz,
        epochs=args.epochs,
        patience=args.patience,
        batch=args.batch,
        nbs=args.nbs,
        device=args.device,
        workers=args.workers,
        cache=False if args.cache == "none" else args.cache,
        project=str(RUNS_DIR),
        name=args.name,
        seed=args.seed,
        deterministic=True,
        # Фасады снимаются с наклоном камеры: вертикальное отражение даёт невозможные кадры.
        fliplr=0.5,
        flipud=0.0,
        # Датасет маленький: мозаика полезна, но последние эпохи учимся на целых кадрах.
        mosaic=1.0,
        close_mosaic=15,
        plots=True,
    )

    run_dir = Path(model.trainer.save_dir)
    best = Path(model.trainer.best)
    WEIGHTS_DIR.mkdir(exist_ok=True)
    final = WEIGHTS_DIR / f"readiness_{Path(args.model).stem}.pt"
    shutil.copy2(best, final)

    metrics = YOLO(str(final)).val(
        data=str(data_yaml),
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        split="val",
        project=str(RUNS_DIR),
        name=f"{args.name}_val",
    )
    names = metrics.names
    per_class = {
        names[int(c)]: round(float(ap), 4)
        for c, ap in zip(metrics.box.ap_class_index, metrics.box.ap50)
    }
    # mAP считается только по классам, которые есть в val: без числа рамок его не прочитать.
    instances = {
        names[int(c)]: int(metrics.nt_per_class[int(c)]) for c in metrics.box.ap_class_index
    }
    report = {
        "model": args.model,
        "weights": str(final.relative_to(REPO)),
        "val_objects": sorted(parse_objects(args.val_objects), key=_object_key),
        "imgsz": args.imgsz,
        "batch": args.batch,
        "nbs": args.nbs,
        "mAP50": round(float(metrics.box.map50), 4),
        "mAP50-95": round(float(metrics.box.map), 4),
        "AP50_per_class": per_class,
        "val_instances": instances,
    }
    (run_dir / "metrics_val.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nвеса: {final}")
    print(f"mAP50 {report['mAP50']}  mAP50-95 {report['mAP50-95']}")
    for name, ap in per_class.items():
        print(f"  {name:<18}{ap:.3f}  (рамок: {instances[name]})")

    if args.export:
        print(YOLO(str(final)).export(format=args.export, imgsz=args.imgsz))


if __name__ == "__main__":
    main()
