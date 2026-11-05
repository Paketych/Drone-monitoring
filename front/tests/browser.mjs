import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const review = path.join(root, ".impeccable/review");
await mkdir(review, { recursive: true });
const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
  args: ["--no-first-run"],
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  locale: "ru-RU",
  timezoneId: "Asia/Barnaul",
});
const page = await context.newPage(),
  errors = [];
const accessibility = [];
async function audit(name) {
  const report = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  accessibility.push({
    name,
    violations: report.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.map((n) => ({ html: n.html, summary: n.failureSummary })),
    })),
  });
}
page.on("pageerror", (e) => errors.push(e.message));
async function ready() {
  await page.locator("main h1").waitFor();
  await page.locator("main .boot").waitFor({ state: "hidden" });
  await page.evaluate(() => document.fonts.ready);
}
async function visit(hash) {
  await page.goto("http://127.0.0.1:4173/" + hash);
  await ready();
}
async function screenshot(name) {
  await page.screenshot({
    path: path.join(review, name),
    fullPage: true,
    animations: "disabled",
  });
}
async function noOverflow() {
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
    "Page overflow",
  );
}
try {
  await visit("#/overview");
  assert.equal(
    await page.getByRole("heading", { name: "Обзор мониторинга" }).count(),
    1,
  );
  await noOverflow();
  await screenshot("desktop.png");
  await audit("overview-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow();
  await screenshot("mobile.png");
  await audit("overview-mobile");
  await page.getByRole("button", { name: "Открыть меню" }).click();
  await page.locator(".nav").getByRole("link", { name: "Объекты" }).click();
  await ready();
  await noOverflow();
  await screenshot("mobile-objects.png");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await visit("#/objects/obj_demo_2");
  await screenshot("object.png");
  await audit("object");
  await page.getByLabel("Не хватает финансирования", { exact: true }).check();
  await page.locator("#step-comment").fill("Подрядчик увеличил стоимость.");
  await page.getByRole("button", { name: "Сохранить исход" }).click();
  await page.getByLabel("Да, подтверждено", { exact: true }).waitFor();
  await page.getByLabel("Да, подтверждено", { exact: true }).check();
  await page.getByRole("button", { name: "Сохранить исход" }).click();
  await page
    .getByRole("button", { name: "Подтвердить", exact: true })
    .waitFor();
  assert.ok(
    await page
      .locator("#strategy")
      .innerText()
      .then((x) => x.includes("S3")),
  );
  await screenshot("strategy-proposed.png");
  await page.getByRole("button", { name: "Отклонить", exact: true }).click();
  await page.getByRole("alert").waitFor();
  assert.ok(
    await page
      .getByRole("alert")
      .innerText()
      .then((x) => x.includes("причину")),
  );
  await page.locator("#strategy-comment").fill("Согласовано с клиентом.");
  await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
  await page.getByText("Подтверждена", { exact: true }).waitFor();
  await page.getByRole("tab", { name: "Журнал", exact: true }).click();
  await page.getByRole("heading", { name: "Журнал действий" }).waitFor();
  assert.ok(
    await page
      .locator("main")
      .innerText()
      .then((x) => x.includes("Согласовано с клиентом.")),
  );
  await visit("#/objects");
  await page.getByLabel("Поиск в реестре").fill("нет-такого-объекта");
  await page.getByRole("button", { name: "Найти", exact: true }).click();
  await page.getByRole("heading", { name: "Объекты не найдены" }).waitFor();
  await screenshot("empty.png");
  await visit("#/import");
  await page
    .locator("#import-file")
    .setInputFiles(path.join(root, "../material/Реестр_для_теста.xlsx"));
  await page
    .getByRole("button", { name: "Импортировать", exact: true })
    .waitFor({ state: "visible" });
  await page.waitForFunction(
    () => !document.querySelector("#import-submit").disabled,
  );
  await screenshot("import-preview.png");
  await page
    .getByRole("button", { name: "Импортировать", exact: true })
    .click();
  await page
    .getByText("Реестр импортирован без ошибок.")
    .waitFor({ timeout: 20000 });
  await screenshot("import-result.png");
  await visit("#/new");
  await page
    .getByLabel("Адрес *", { exact: true })
    .fill("Тестовый объект, ул. Проверочная, 1");
  await page
    .getByLabel("Кадастровый номер *", { exact: true })
    .fill("54:19:999:100");
  await page
    .getByLabel("Дата начала строительства *", { exact: true })
    .fill("2026-01-01");
  await page
    .getByLabel("Плановое завершение *", { exact: true })
    .fill("2028-01-01");
  await page
    .getByRole("button", { name: "Добавить объект", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Тестовый объект, ул. Проверочная, 1" })
    .waitFor();
  await visit("#/upload/obj_demo_2");
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jS1sAAAAASUVORK5CYII=",
    "base64",
  );
  await page.locator("#flight-files").setInputFiles(
    Array.from({ length: 6 }, (_, i) => ({
      name: `test-${i}.png`,
      mimeType: "image/png",
      buffer: png,
    })),
  );
  for (const [i, v] of ["N", "E", "S", "W", "top_low", "top_high"].entries())
    await page.locator(`[data-file-view="${i}"]`).selectOption(v);
  await page.getByRole("button", { name: "Отправить на обработку" }).click();
  await page.getByRole("heading", { name: "Обрабатываем облёт" }).waitFor();
  await page.waitForURL(/#\/objects\/obj_demo_2/, { timeout: 15000 });
  await ready();
  await page.getByRole("tab", { name: "Снимки", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelectorAll(".photo-tile img").length === 6,
  );
  assert.equal(await page.locator(".photo-tile img").count(), 6);
  await page.locator(".photo-tile").first().click();
  await page.getByRole("dialog").waitFor();
  await page.getByLabel("Показать рамки").uncheck();
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  await page.getByRole("tab", { name: "Обзор", exact: true }).click();
  await page.getByRole("button", { name: "Исправить", exact: true }).click();
  await page.getByLabel("Стадия", { exact: true }).selectOption("walls");
  await page.getByLabel("Готовность, %", { exact: true }).fill("40");
  await page.getByLabel("Причина исправления").fill("Тест ручного ревью");
  await page.getByRole("button", { name: "Сохранить правку" }).click();
  await page.getByText("Результат проверен: Сотрудник").waitFor();
  await page.getByRole("link", { name: "Акт осмотра", exact: true }).click();
  await page.getByLabel("Количество комнат", { exact: false }).fill("3");
  await screenshot("act.png");
  await page
    .getByRole("button", { name: "Подключение и профиль", exact: true })
    .click();
  await page.getByLabel("Источник данных").selectOption("api");
  await page.getByRole("button", { name: "Применить", exact: true }).click();
  await page.getByRole("dialog").getByRole("alert").waitFor();
  await screenshot("api-error.png");
  console.log(
    "API error:",
    await page.getByRole("dialog").getByRole("alert").innerText(),
  );
  assert.ok(
    await page
      .getByRole("dialog")
      .getByRole("alert")
      .innerText()
      .then((x) => x.includes("API не подключён")),
  );
  assert.equal(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem("sibcode.front.settings")).mode,
    ),
    "demo",
  );
  await page
    .getByRole("button", { name: "Закрыть", exact: true })
    .first()
    .click();
  await page
    .getByRole("button", { name: "Подключение и профиль", exact: true })
    .click();
  await page.getByText("Восстановить сценарий демонстрации").click();
  await page.getByRole("button", { name: "Сбросить демо-данные" }).click();
  await ready();
  await visit("#/objects/obj_demo_6");
  await page.getByRole("tab", { name: "Участок", exact: true }).click();
  await page.getByRole("heading", { name: "Освоение участка" }).waitFor();
  await screenshot("land.png");
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await visit("#/overview");
    await noOverflow();
    await visit("#/objects/obj_demo_2");
    await noOverflow();
    await visit("#/upload/obj_demo_2");
    await noOverflow();
  }
  await writeFile(
    path.join(review, "accessibility.json"),
    JSON.stringify(accessibility, null, 2),
  );
  console.log(
    "Accessibility findings:",
    accessibility.map((x) => ({
      name: x.name,
      violations: x.violations.length,
    })),
  );
  assert.deepEqual(errors, [], "Browser errors");
  assert.ok(
    accessibility.every((x) => x.violations.length === 0),
    "Accessibility checks must pass",
  );
  await writeFile(
    path.join(review, "browser-result.json"),
    JSON.stringify(
      {
        passed: true,
        viewports: [1440, 1280, 390],
        flows: [
          "S3 proposal and confirmation",
          "reject reason validation",
          "journal",
          "empty filters",
          "real XLSX registry import",
          "create object",
          "upload six photos and poll",
          "gallery",
          "correction audit",
          "act fields",
          "API failure does not switch mode",
          "reset demo",
        ],
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Browser checks passed. Screenshots:", review);
} finally {
  await browser.close();
}
