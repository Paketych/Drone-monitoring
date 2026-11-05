"""Обучение классификатора материала стен (docs/archive/BACKEND_PLAN.md, разделы 5.1–5.7).

    python ml/train_material.py                          # YOLO26m-cls, imgsz 384, 60 эпох
    python ml/train_material.py --val-objects 6,house_101,house_110
    python ml/train_material.py --epochs 1 --name material_smoke

Кропы берутся из той же разметки, что и детектор: рамки walls_partial / walls_full с материалом
(configs/materials.yaml), с полем 10% вокруг стены. Val — те же объекты, что у детектора
(ml/annotations/val_objects.txt). Стены с материалом unclear в обучение не идут: это не материал,
а сомнение разметчика, в сервисе unclear получается из низкой уверенности (min_crop_confidence).
Кропы — data/material/{train,val}/<материал>/, лучшие веса — weights/material_<модель>.pt,
метрики val — runs/<name>/metrics_val.json: точность по классам, средняя по классам и по группам.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import torch
import yaml
from common import (
    MATERIALS_YAML,
    REPO,
    STATUS_REVIEWED,
    load_classes,
    load_materials,
    load_val_objects,
    read_labels,
    read_manifest,
)
from PIL import Image
from prepare_dataset import _object_key, parse_objects
from train import RUNS_DIR, WEIGHTS_DIR, resolve_model
from ultralytics import YOLO

OUT_DIR = REPO / "data" / "material"
# Общие с сервисом (agents/material/yolo.py): поле вокруг стены, раздел 5.1, и минимальный
# размер вырезки по короткой стороне — мельче текстуру стены не разобрать.
_CROP_RULES = yaml.safe_load(MATERIALS_YAML.read_text(encoding="utf-8"))
CROP_MARGIN = float(_CROP_RULES["crop_margin"])
MIN_CROP_PX = int(_CROP_RULES["min_crop_px"])
NOT_TRAINED = {"unclear"}


def collect_crops() -> list[dict]:
    """Все размеченные стены: снимок, рамка, материал, объект."""
    names = [c["name"] for c in load_classes()]
    _, wall_classes = load_materials()
    crops = []
    for row in read_manifest():
        if row["status"] != STATUS_REVIEWED or not (REPO / row["image"]).exists():
            continue
        for i, b in enumerate(read_labels(row["image"])):
            if names[b.cls] in wall_classes and b.material and b.material not in NOT_TRAINED:
                crops.append(
                    {
                        "image": row["image"],
                        "object": row["object"],
                        "idx": i,
                        "box": b,
                        "material": b.material,
                    }
                )
    return crops


def build(
    val_objects: set[str], out: Path = OUT_DIR, oversample: dict[str, int] | None = None
) -> tuple[Path, Counter, Counter]:
    crops = collect_crops()
    shutil.rmtree(out, ignore_errors=True)
    stats = {"train": Counter(), "val": Counter()}
    opened: dict[str, Image.Image] = {}
    skipped = 0
    for c in crops:
        im = opened.get(c["image"])
        if im is None:
            opened.clear()  # держим в памяти один снимок: исходники по 9 МБ
            im = opened[c["image"]] = Image.open(REPO / c["image"]).convert("RGB")
        W, H = im.size
        b = c["box"]
        x1, y1 = (b.cx - b.w * (0.5 + CROP_MARGIN)) * W, (b.cy - b.h * (0.5 + CROP_MARGIN)) * H
        x2, y2 = (b.cx + b.w * (0.5 + CROP_MARGIN)) * W, (b.cy + b.h * (0.5 + CROP_MARGIN)) * H
        box = (max(0, round(x1)), max(0, round(y1)), min(W, round(x2)), min(H, round(y2)))
        if min(box[2] - box[0], box[3] - box[1]) < MIN_CROP_PX:
            skipped += 1
            continue
        split = "val" if c["object"] in val_objects else "train"
        d = out / split / c["material"]
        d.mkdir(parents=True, exist_ok=True)
        name = f"{c['object']}_{Path(c['image']).stem}_{c['idx']}.jpg"
        im.crop(box).save(d / name, quality=95)
        stats[split][c["material"]] += 1
        # Редкий материал повторяется в train: аугментации дают каждой копии свой вид.
        for k in range(1, (oversample or {}).get(c["material"], 1) if split == "train" else 1):
            shutil.copyfile(d / name, d / f"{Path(name).stem}_x{k}.jpg")
    if skipped:
        print(f"пропущено мелких кропов (< {MIN_CROP_PX} px): {skipped}")
    return out, stats["train"], stats["val"]


def evaluate(weights: Path, root: Path, imgsz: int, device: str) -> dict:
    """Точность по классам и по группам на val: для сигнала MATERIAL_MISMATCH важна именно группа (раздел 5.7)."""
    group = {
        m["name"]: m["group"]
        for m in yaml.safe_load(MATERIALS_YAML.read_text(encoding="utf-8"))["materials"]
    }
    model = YOLO(str(weights))
    hit, total, group_hit, group_total = Counter(), Counter(), 0, 0
    confusion: dict[str, Counter] = defaultdict(Counter)
    for d in sorted((root / "val").iterdir()):
        files = sorted(d.glob("*.jpg"))
        for k in range(0, len(files), 32):
            for res in model.predict(
                [str(f) for f in files[k : k + 32]], imgsz=imgsz, device=device, verbose=False
            ):
                pred = res.names[int(res.probs.top1)]
                total[d.name] += 1
                hit[d.name] += pred == d.name
                confusion[d.name][pred] += 1
                if group.get(d.name):  # hidden / unclear в сравнение групп не входят
                    group_total += 1
                    group_hit += group.get(pred) == group[d.name]
    return {
        "top1": round(sum(hit.values()) / max(1, sum(total.values())), 4),
        # top1 держится на самом частом классе hidden; средняя по классам этого не прячет.
        "macro_accuracy": round(sum(hit[m] / total[m] for m in total) / max(1, len(total)), 4),
        "group_accuracy": round(group_hit / max(1, group_total), 4),
        "per_class": {m: {"n": total[m], "acc": round(hit[m] / total[m], 4)} for m in total},
        "confusion": {m: dict(c) for m, c in confusion.items()},
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--model", default="yolo26m-cls.pt")
    p.add_argument("--imgsz", type=int, default=384, help="раздел 5.7")
    p.add_argument("--epochs", type=int, default=60)
    p.add_argument("--patience", type=int, default=20)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--device", default="0" if torch.cuda.is_available() else "cpu")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument(
        "--val-objects",
        default=load_val_objects(),
        help="объекты для val через запятую; по умолчанию ml/annotations/val_objects.txt",
    )
    p.add_argument("--name", default="material")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--oversample",
        default="brick=3,timber=3,frame=5,ceramic_block=8",
        help="во сколько раз повторить вырезки материала в train, через запятую; '' — без повторов. "
        "Редких материалов в 5–20 раз меньше, чем «скрыт отделкой»: без повторов они уходят в hidden",
    )
    args = p.parse_args()
    oversample = {
        m: int(k) for m, _, k in (x.partition("=") for x in args.oversample.split(",") if x)
    }

    val_objects = parse_objects(args.val_objects)
    root, train_stats, val_stats = build(val_objects, oversample=oversample)
    print(f"val-объекты: {', '.join(sorted(val_objects, key=_object_key))}")
    print(f"{'материал':<18}{'train':>7}{'val':>7}")
    for m in sorted(set(train_stats) | set(val_stats), key=lambda m: -train_stats[m]):
        print(f"{m:<18}{train_stats[m]:>7}{val_stats[m]:>7}")

    model = YOLO(resolve_model(args.model))
    model.train(
        data=str(root),
        imgsz=args.imgsz,
        epochs=args.epochs,
        patience=args.patience,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        project=str(RUNS_DIR),
        name=args.name,
        seed=args.seed,
        deterministic=True,
        # Снимки в разное время дня — сильнее цвет и яркость; без поворотов: у бруса и кладки важна ориентация.
        hsv_h=0.02,
        hsv_s=0.6,
        hsv_v=0.5,
        degrees=0.0,
        fliplr=0.5,
        flipud=0.0,
        plots=True,
    )

    run_dir = Path(model.trainer.save_dir)
    final = WEIGHTS_DIR / f"material_{Path(args.model).stem}.pt"
    WEIGHTS_DIR.mkdir(exist_ok=True)
    shutil.copy2(model.trainer.best, final)

    report = {
        "model": args.model,
        "weights": str(final.relative_to(REPO)),
        "val_objects": sorted(val_objects, key=_object_key),
        "imgsz": args.imgsz,
        "train_crops": dict(train_stats),
        "val_crops": dict(val_stats),
        **evaluate(final, root, args.imgsz, args.device),
    }
    (run_dir / "metrics_val.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nвеса: {final}")
    print(
        f"точность top1 {report['top1']}  средняя по классам {report['macro_accuracy']}"
        f"  по группам {report['group_accuracy']}"
    )
    for m, v in report["per_class"].items():
        print(f"  {m:<18}{v['acc']:.3f}  (n={v['n']})")


if __name__ == "__main__":
    main()
