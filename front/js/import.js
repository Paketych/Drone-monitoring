// CSV и XLSX без библиотек. XLSX: ZIP + XML; формулы не исполняются.
export function parseCSV(text) {
  const separator =
    (text.split(/\r?\n/, 1)[0].match(/;/g) || []).length >
    (text.split(/\r?\n/, 1)[0].match(/,/g) || []).length
      ? ";"
      : ",";
  const rows = [];
  let row = [],
    cell = "",
    quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = !quoted;
    } else if (c === separator && !quoted) {
      row.push(cell.trim());
      cell = "";
    } else if ((c === "\n" || c === "\r") && !quoted) {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell.trim());
      if (row.some(Boolean)) rows.push(row);
      row = [];
      cell = "";
    } else cell += c;
  }
  if (quoted) throw new Error("В CSV не закрыта кавычка. Проверьте файл.");
  row.push(cell.trim());
  if (row.some(Boolean)) rows.push(row);
  return rows;
}
async function unzip(buffer) {
  const data = new DataView(buffer),
    bytes = new Uint8Array(buffer),
    decoder = new TextDecoder();
  let end = bytes.length - 22;
  while (
    end >= Math.max(0, bytes.length - 65557) &&
    data.getUint32(end, true) !== 0x06054b50
  )
    end--;
  if (end < 0) throw new Error("Файл не является корректным XLSX.");
  const files = new Map();
  let pos = data.getUint32(end + 16, true),
    total = 0;
  for (let i = 0; i < data.getUint16(end + 10, true); i++) {
    if (data.getUint32(pos, true) !== 0x02014b50)
      throw new Error("Повреждена структура XLSX.");
    const method = data.getUint16(pos + 10, true),
      size = data.getUint32(pos + 20, true),
      plainSize = data.getUint32(pos + 24, true),
      n = data.getUint16(pos + 28, true),
      extra = data.getUint16(pos + 30, true),
      comment = data.getUint16(pos + 32, true),
      offset = data.getUint32(pos + 42, true);
    total += plainSize;
    if (total > 40 * 1024 * 1024)
      throw new Error("Распакованный XLSX слишком большой. Разделите реестр.");
    const name = decoder.decode(bytes.slice(pos + 46, pos + 46 + n));
    pos += 46 + n + extra + comment;
    if (!name.endsWith(".xml") && !name.endsWith(".rels")) continue;
    const start =
      offset +
      30 +
      data.getUint16(offset + 26, true) +
      data.getUint16(offset + 28, true);
    let content = bytes.slice(start, start + size);
    if (method === 8) {
      if (!globalThis.DecompressionStream)
        throw new Error(
          "Браузер не поддерживает распаковку XLSX. Сохраните реестр как CSV.",
        );
      content = new Uint8Array(
        await new Response(
          new Blob([content])
            .stream()
            .pipeThrough(new DecompressionStream("deflate-raw")),
        ).arrayBuffer(),
      );
    } else if (method !== 0)
      throw new Error(
        "Этот способ сжатия XLSX не поддерживается. Сохраните реестр как CSV.",
      );
    files.set(name, decoder.decode(content));
  }
  return files;
}
const xml = (s) => {
  const doc = new DOMParser().parseFromString(s || "", "application/xml");
  if (doc.querySelector("parsererror"))
    throw new Error("В XLSX повреждены XML-данные.");
  return doc;
};
function column(ref) {
  let n = 0;
  for (const c of ref.replace(/\d/g, "")) n = n * 26 + c.charCodeAt(0) - 64;
  return n - 1;
}
export async function parseXLSX(buffer) {
  const files = await unzip(buffer),
    strings = files.has("xl/sharedStrings.xml")
      ? [
          ...xml(files.get("xl/sharedStrings.xml")).getElementsByTagName("si"),
        ].map((si) =>
          [...si.getElementsByTagName("t")].map((t) => t.textContent).join(""),
        )
      : [];
  const workbook = xml(files.get("xl/workbook.xml")),
    sheet = workbook.getElementsByTagName("sheet")[0],
    rid = sheet?.getAttribute("r:id");
  const rels = xml(files.get("xl/_rels/workbook.xml.rels"));
  const rel = [...rels.getElementsByTagName("Relationship")].find(
    (x) => x.getAttribute("Id") === rid,
  );
  const target = rel?.getAttribute("Target") || "worksheets/sheet1.xml";
  const filename = target.startsWith("/") ? target.slice(1) : `xl/${target}`;
  if (!files.has(filename)) throw new Error("В XLSX не найден первый лист.");
  return [...xml(files.get(filename)).getElementsByTagName("row")]
    .map((r) => {
      const row = [];
      for (const c of r.getElementsByTagName("c")) {
        const type = c.getAttribute("t"),
          v = c.getElementsByTagName("v")[0]?.textContent;
        row[column(c.getAttribute("r") || "A1")] =
          type === "s"
            ? strings[Number(v)]
            : type === "inlineStr"
              ? [...c.getElementsByTagName("t")]
                  .map((t) => t.textContent)
                  .join("")
              : (v ?? "");
      }
      return row;
    })
    .filter((r) => r.some((x) => String(x).trim()));
}
const required = [
  "Территория",
  "Кадастровый номер",
  "Плановая дата",
  "Фактическая дата",
  "Наличие ПЗ",
  "ОН зарегистрирован",
  "Залог",
  "Дата начала строительства",
  "Наличие фото с неба",
];
function date(value) {
  if (
    value == null ||
    String(value).trim() === "" ||
    String(value).toLowerCase() === "нет"
  )
    return null;
  const s = String(value).trim();
  if (/^\d+(\.\d+)?$/.test(s))
    return new Date(Date.UTC(1899, 11, 30) + Number(s) * 86400000)
      .toISOString()
      .slice(0, 10);
  const m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
  const iso = m
    ? `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`
    : s.slice(0, 10);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(iso) ||
    !Number.isFinite(Date.parse(iso)) ||
    new Date(iso).toISOString().slice(0, 10) !== iso
  )
    return null;
  return iso;
}
export function registryRows(table) {
  if (table.length < 2)
    throw new Error(
      "Реестр пуст. Нужны строка заголовков и хотя бы один объект.",
    );
  const headers = table[0].map((x) =>
      String(x)
        .replace(/^\uFEFF/, "")
        .trim(),
    ),
    missing = required.filter((h) => !headers.includes(h));
  if (missing.length)
    throw new Error(`Не найдены колонки: ${missing.join(", ")}.`);
  const get = (row, h) => row[headers.indexOf(h)];
  const bool = (v) => String(v).trim().toLowerCase() === "да";
  return table.slice(1).map((r, i) => ({
    address: String(get(r, "Территория") || "").trim(),
    cadastral: String(get(r, "Кадастровый номер") || "").trim(),
    planned_completion_at: date(get(r, "Плановая дата")),
    actual_completion_at: date(get(r, "Фактическая дата")),
    started_at: date(get(r, "Дата начала строительства")),
    overdue: bool(get(r, "Наличие ПЗ")),
    registered: bool(get(r, "ОН зарегистрирован")),
    pledged: bool(get(r, "Залог")),
    has_aerial_photo: bool(get(r, "Наличие фото с неба")),
    _row: i + 2,
  }));
}
export async function readRegistry(file) {
  if (file.size > 10 * 1024 * 1024)
    throw new Error("Реестр больше 10 МБ. Разделите файл.");
  if (/\.xlsx$/i.test(file.name))
    return registryRows(await parseXLSX(await file.arrayBuffer()));
  if (/\.csv$/i.test(file.name))
    return registryRows(parseCSV(await file.text()));
  throw new Error("Выберите файл XLSX или CSV.");
}
