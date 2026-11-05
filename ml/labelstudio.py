"""Разметка YOLO <-> Label Studio.

    python ml/labelstudio.py config               # интерфейс разметки -> ml/annotations/labelstudio_config.xml
    python ml/labelstudio.py export               # YOLO -> ml/annotations/labelstudio_tasks.json
    python ml/labelstudio.py import export.json   # выгрузка Label Studio (формат JSON) -> YOLO + manifest

Проверенные снимки (status reviewed) уходят как аннотации, непроверенные рамки модели (prelabel) —
как predictions, снимки со статусом skip не выгружаются. После импорта снимок получает статус reviewed.
Материал стены выбирается у выделенной рамки walls_partial / walls_full.
Снимки отдаются через Local Storage Label Studio, подробности в ml/README.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.sax.saxutils import quoteattr

import yaml
from common import (
    ANNOTATIONS,
    MATERIALS_YAML,
    STATUS_PRELABEL,
    STATUS_REVIEWED,
    STATUS_SKIP,
    VIEWS,
    Box,
    load_classes,
    load_materials,
    read_labels,
    read_manifest,
    write_labels,
    write_manifest,
)

CONFIG_XML = ANNOTATIONS / "labelstudio_config.xml"
TASKS_JSON = ANNOTATIONS / "labelstudio_tasks.json"
DEFAULT_URL_PREFIX = "/data/local-files/?d="


def build_config() -> str:
    labels = "\n".join(
        f'    <Label value="{c["name"]}" hint={quoteattr(c["label_ru"])} background="{c["color"]}"/>'
        for c in load_classes()
    )
    materials = "\n".join(
        f'    <Choice value="{m["name"]}" hint={quoteattr(m["label_ru"])}/>'
        for m in yaml.safe_load(MATERIALS_YAML.read_text(encoding="utf-8"))["materials"]
    )
    views = "\n".join(f'    <Choice value="{v}"/>' for v in VIEWS)
    _, wall_classes = load_materials()
    walls = ",".join(wall_classes)
    return f"""<View>
  <Header value="$title"/>
  <Image name="image" value="$image" zoom="true" zoomControl="true" rotateControl="false"/>
  <RectangleLabels name="label" toName="image" strokeWidth="2" canRotate="false">
{labels}
  </RectangleLabels>
  <View visibleWhen="region-selected" whenTagName="label" whenLabelValue="{walls}"
        style="padding: 8px 12px; margin-top: 8px; border: 2px solid #E53935; border-radius: 6px">
    <Header value="Материал выделенной стены" size="5"/>
    <Choices name="material" toName="image" perRegion="true" choice="single" showInline="true">
{materials}
    </Choices>
  </View>
  <Choices name="view" toName="image" choice="single" showInline="true">
{views}
  </Choices>
</View>
"""


def _region(region_id: str, w: int, h: int, b: Box, from_name: str, value: dict) -> dict:
    return {
        "id": region_id,
        "type": "choices" if from_name == "material" else "rectanglelabels",
        "from_name": from_name,
        "to_name": "image",
        "original_width": w,
        "original_height": h,
        "image_rotation": 0,
        "value": {
            "x": round((b.cx - b.w / 2) * 100, 4),
            "y": round((b.cy - b.h / 2) * 100, 4),
            "width": round(b.w * 100, 4),
            "height": round(b.h * 100, 4),
            "rotation": 0,
            **value,
        },
    }


def export_tasks(url_prefix: str, as_predictions: bool) -> list[dict]:
    names = [c["name"] for c in load_classes()]
    tasks = []
    for row in read_manifest():
        if row["status"] == STATUS_SKIP:
            continue
        w, h = int(row["width"]), int(row["height"])
        prefix = f"{row['object']}_{Path(row['image']).stem}"
        result: list[dict] = []
        for i, b in enumerate(read_labels(row["image"])):
            rid = f"{prefix}_{i}"
            result.append(_region(rid, w, h, b, "label", {"rectanglelabels": [names[b.cls]]}))
            if b.material:
                result.append(_region(rid, w, h, b, "material", {"choices": [b.material]}))
        result.append(
            {
                "id": f"{prefix}_view",
                "type": "choices",
                "from_name": "view",
                "to_name": "image",
                "value": {"choices": [row["view"]]},
            }
        )
        task = {
            "data": {
                "image": url_prefix + row["image"],
                "path": row["image"],
                "object": row["object"],
                "view": row["view"],
                "status": row["status"],
                "title": f"Объект {row['object']} · {row['view']} · {Path(row['image']).name} · {row['note']}",
            }
        }
        if as_predictions or row["status"] == STATUS_PRELABEL:
            task["predictions"] = [{"model_version": "prelabel", "result": result}]
        else:
            task["annotations"] = [{"result": result}]
        tasks.append(task)
    return tasks


def import_export(path: Path) -> None:
    """Забирает правки из выгрузки Label Studio (Export -> JSON) обратно в labels/ и manifest.csv."""
    name_to_id = {c["name"]: c["id"] for c in load_classes()}
    manifest = {row["image"]: row for row in read_manifest()}
    updated, skipped = 0, []
    for task in json.loads(path.read_text(encoding="utf-8")):
        image = task["data"].get("path")
        if image not in manifest:
            raise KeyError(f"задача {task.get('id')}: снимка {image!r} нет в manifest.csv")
        annotations = [a for a in task.get("annotations", []) if not a.get("was_cancelled")]
        if not annotations:
            skipped.append(image)
            continue
        latest = max(annotations, key=lambda a: a.get("updated_at") or a.get("created_at") or "")
        rects: dict[str, tuple] = {}
        materials: dict[str, str] = {}
        for r in latest["result"]:
            v = r["value"]
            if r["from_name"] == "view":
                manifest[image]["view"] = v["choices"][0]
            elif r["from_name"] == "material":
                materials[r["id"]] = v["choices"][0]
            elif r["type"] == "rectanglelabels":
                if v.get("rotation"):
                    raise ValueError(f"{image}: повёрнутая рамка, YOLO-det её не поддерживает")
                x1, y1 = max(v["x"], 0) / 100, max(v["y"], 0) / 100
                x2, y2 = min(v["x"] + v["width"], 100) / 100, min(v["y"] + v["height"], 100) / 100
                rects[r["id"]] = (
                    name_to_id[v["rectanglelabels"][0]],
                    (x1 + x2) / 2,
                    (y1 + y2) / 2,
                    x2 - x1,
                    y2 - y1,
                )
        boxes = [Box(*rect, materials.get(rid, "")) for rid, rect in rects.items()]
        write_labels(image, boxes)
        manifest[image]["boxes"] = str(len(boxes))
        manifest[image]["status"] = STATUS_REVIEWED
        updated += 1
    write_manifest(list(manifest.values()))
    print(f"обновлено снимков: {updated}")
    if skipped:
        print(f"без разметки, оставлены как были ({len(skipped)}):", *skipped, sep="\n  ")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config", help="записать интерфейс разметки")
    exp = sub.add_parser("export", help="YOLO -> задачи Label Studio")
    exp.add_argument(
        "--url-prefix",
        default=DEFAULT_URL_PREFIX,
        help="префикс URL снимка; путь снимка из manifest.csv добавляется к нему (по умолчанию Local Storage)",
    )
    exp.add_argument(
        "--as-predictions",
        action="store_true",
        help="положить все рамки в predictions (предразметка на проверку), а не в annotations",
    )
    exp.add_argument("--out", type=Path, default=TASKS_JSON)
    imp = sub.add_parser("import", help="выгрузка Label Studio -> YOLO")
    imp.add_argument("export_json", type=Path)
    args = parser.parse_args()

    if args.cmd == "config":
        CONFIG_XML.write_text(build_config(), encoding="utf-8")
        print(CONFIG_XML)
    elif args.cmd == "export":
        tasks = export_tasks(args.url_prefix, args.as_predictions)
        args.out.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{args.out}: {len(tasks)} задач")
    else:
        import_export(args.export_json)


if __name__ == "__main__":
    main()
