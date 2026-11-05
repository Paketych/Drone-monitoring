import { mockRequest, importDemoRows } from "./mock.js";
export function settings() {
  try {
    const value = {
      mode: "demo",
      user: "ivanov",
      display_name: "Сотрудник",
      ...JSON.parse(localStorage.getItem("sibcode.front.settings") || "{}"),
    };
    if (!/^[a-zA-Z0-9_.@-]+$/.test(value.user)) {
      value.display_name = value.user;
      value.user = "ivanov";
    }
    return value;
  } catch {
    return { mode: "demo", user: "ivanov", display_name: "Сотрудник" };
  }
}
export function saveSettings(value) {
  localStorage.setItem("sibcode.front.settings", JSON.stringify(value));
}
export async function request(path, options = {}) {
  const s = settings();
  if (s.mode === "demo")
    return mockRequest(path, { ...options, user: s.display_name || s.user });
  const form = options.body instanceof FormData;
  const headers = {
    "X-User": s.user,
    Accept: "application/json",
    ...(form ? {} : { "Content-Type": "application/json" }),
  };
  let response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method: options.method || "GET",
      headers,
      body: options.body
        ? form
          ? options.body
          : JSON.stringify(options.body)
        : undefined,
      signal: AbortSignal.timeout(30000),
    });
  } catch {
    throw new Error(
      "Не удалось связаться с API. Проверьте подключение и повторите запрос.",
    );
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const e = new Error(
      data?.error?.message ||
        `Ошибка API (${response.status}). Повторите запрос.`,
    );
    e.code = data?.error?.code;
    throw e;
  }
  if (data === null)
    throw new Error(
      "API вернул ответ в неподдерживаемом формате. Ожидается JSON.",
    );
  return data;
}
export async function allObjects() {
  let page = 1,
    items = [],
    total = 0;
  do {
    const batch = await request(`/objects?page_size=100&page=${page++}`);
    if (!Array.isArray(batch.items))
      throw new Error("В ответе API отсутствует список объектов.");
    items.push(...batch.items);
    total = batch.total ?? items.length;
    if (!batch.items.length) break;
  } while (items.length < total && page < 1000);
  return items;
}
export async function importRegistry(file, rows) {
  if (settings().mode === "demo") return importDemoRows(rows, settings().user);
  const body = new FormData();
  body.append("file", file);
  return request("/objects/import", { method: "POST", body });
}
