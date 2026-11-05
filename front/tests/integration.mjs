// Starts the actual API, queue worker and frontend proxy with an isolated database.
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const python = path.join(root, ".venv/Scripts/python.exe");
const folder = path.join(
  root,
  ".local",
  `i${Date.now().toString(36).slice(-6)}`,
);
await mkdir(folder, { recursive: true });
const children = [],
  logs = [],
  failures = [],
  forbidden = [];
let browser;
async function port() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const value = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return value;
}
function start(command, args, env) {
  const child = spawn(command, args, {
    cwd: root,
    env: { ...process.env, ...env },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  children.push(child);
  child.stdout.on("data", (data) => logs.push(data.toString()));
  child.stderr.on("data", (data) => logs.push(data.toString()));
  child.on("error", (error) => logs.push(String(error)));
  return child;
}
async function wait(url) {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Server did not start: ${url}`);
}
try {
  const apiPort = await port(),
    frontPort = await port();
  const env = {
    STORAGE_DIR: path.join(folder, "db"),
    MOCK: "1",
    LLM_ENABLED: "0",
    PYTHONIOENCODING: "utf-8",
  };
  start(
    python,
    [
      "-m",
      "uvicorn",
      "api.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(apiPort),
    ],
    env,
  );
  await wait(`http://127.0.0.1:${apiPort}/api/v1/health`);
  start(python, ["-m", "worker"], env);
  start(process.execPath, ["front/server.mjs"], {
    PORT: String(frontPort),
    API_TARGET: `http://127.0.0.1:${apiPort}`,
  });
  const base = `http://127.0.0.1:${frontPort}`;
  await wait(base);
  execFileSync(python, [
    "-c",
    "from pathlib import Path; from PIL import Image; import sys; p=Path(sys.argv[1]); ex=Image.Exif(); ex[306]='2026:10:31 10:30:00'; [(Image.effect_noise((400,300),60+i).convert('RGB').save(p/f'{i}.jpg','JPEG',exif=ex if i==2 else Image.Exif())) for i in range(3)]",
    folder,
  ]);
  browser = await chromium.launch({
    channel: "chrome",
    headless: true,
    args: ["--no-first-run"],
  });
  const context = await browser.newContext({
    locale: "ru-RU",
    viewport: { width: 1440, height: 1000 },
  });
  await context.addInitScript(() => {
    if (location.protocol === "http:")
      localStorage.setItem(
        "sibcode.front.settings",
        JSON.stringify({
          mode: "api",
          user: "integration",
          display_name: "Проверяющий",
        }),
      );
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("request", (request) => {
    if (/\/api\/v1\/(alerts|tasks)(\?|$)/.test(request.url()))
      forbidden.push(request.url());
  });
  page.on("response", (response) => {
    if (
      response.status() >= 400 &&
      response.url().includes("/api/v1/") &&
      !(response.status() === 409 && response.request().method() === "DELETE")
    )
      failures.push(`${response.status()} ${response.url()}`);
  });
  await page.goto(base);
  await page
    .getByRole("heading", { name: "Обзор мониторинга", exact: true })
    .waitFor();
  await page.goto(base + "/#/new");
  await page.getByLabel("Адрес *", { exact: true }).fill("Интеграционный дом");
  await page
    .getByLabel("Кадастровый номер *", { exact: true })
    .fill("54:19:1:17");
  await page
    .getByLabel("Дата начала строительства *", { exact: true })
    .fill("2026-01-01");
  await page
    .getByLabel("Плановое завершение *", { exact: true })
    .fill("2027-10-31");
  await page
    .getByRole("button", { name: "Добавить объект", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Интеграционный дом", exact: true })
    .waitFor();
  const objectId = new URL(page.url()).hash.split("/").at(-1);
  await page
    .getByRole("button", { name: "Загрузить облёт", exact: true })
    .click();
  await page
    .locator("#flight-files")
    .setInputFiles([0, 1, 2].map((i) => path.join(folder, `${i}.jpg`)));
  await page.locator('[data-file-view="0"]').selectOption("N");
  await page.locator('[data-file-view="1"]').selectOption("N");
  await page
    .getByRole("checkbox", {
      name: "Я проверил, что на фотографиях нужное здание",
    })
    .check();
  await page
    .getByRole("button", { name: "Отправить на обработку", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Интеграционный дом", exact: true })
    .waitFor({ timeout: 20000 });
  let card = await (
    await page.request.get(`${base}/api/v1/objects/${objectId}`)
  ).json();
  assert.equal(card.last_flight.target_confirmed, true);
  assert.equal(card.last_flight.result.status, "model_unavailable");
  await page.getByRole("button", { name: "Исправить", exact: true }).click();
  await page.getByLabel("Стадия", { exact: true }).selectOption("walls");
  await page.getByLabel("Готовность, %", { exact: true }).fill("45");
  await page.getByLabel("Шкала банка", { exact: true }).selectOption("50");
  await page
    .getByLabel("Причина исправления", { exact: true })
    .fill("Проверено сотрудником");
  await page
    .getByRole("button", { name: "Сохранить правку", exact: true })
    .click();
  await page
    .getByText("Проверено сотрудником", { exact: true })
    .first()
    .waitFor();
  await page.getByRole("tab", { name: "Материал стен", exact: true }).click();
  await page
    .getByRole("button", { name: "Указать материал вручную", exact: true })
    .click();
  await page.getByLabel("Материал стен", { exact: true }).selectOption("brick");
  await page
    .getByLabel("Основание исправления", { exact: true })
    .fill("Кирпич подтверждён по документам");
  await page
    .getByRole("button", { name: "Сохранить материал", exact: true })
    .click();
  await page
    .getByText("Материал проверен сотрудником", { exact: false })
    .waitFor();
  await page.getByRole("tab", { name: "Снимки", exact: true }).click();
  await page.locator(".photo-tile").nth(2).waitFor();
  assert.equal(await page.locator(".photo-tile").count(), 3);
  assert.equal(
    await page.getByText("Ракурс не указан", { exact: true }).count(),
    1,
  );
  card = await (
    await page.request.get(`${base}/api/v1/objects/${objectId}`)
  ).json();
  assert.equal(card.material.observed, "brick");
  assert.equal(card.stage, "walls");
  assert.equal(card.readiness_pct, 45);
  assert.equal(card.data_status, "ok");
  assert.equal(card.last_flight.review.stage, "walls");
  assert.equal(card.last_flight.material_review.material, "brick");
  const history = await (
    await page.request.get(`${base}/api/v1/objects/${objectId}/history`)
  ).json();
  assert.equal(history.fact[0].readiness_pct, 45);
  const actions = await (
    await page.request.get(`${base}/api/v1/objects/${objectId}/actions`)
  ).json();
  assert.equal(actions.length, 2);
  await page
    .getByRole("tab", { name: "Стоимость постройки", exact: true })
    .click();
  await page
    .getByLabel("Общая площадь для расчёта, м²", { exact: true })
    .fill("100");
  const calculated = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/objects/${objectId}/valuation`),
  );
  await page
    .getByRole("button", {
      name: "Сохранить параметры и пересчитать",
      exact: true,
    })
    .click();
  const costResponse = await calculated;
  assert.equal(costResponse.status(), 200);
  const cost = await costResponse.json();
  assert.equal(cost.full_cost_rub, 6000000);
  assert.equal(cost.completed_cost_rub, 2700000);
  assert.equal(cost.remaining_cost_rub, 3300000);
  await page
    .getByRole("heading", { name: "Стоимость постройки", exact: true })
    .waitFor();
  assert.match(
    await page.locator('[data-cost="remaining_cost_rub"]').innerText(),
    /3\s*300\s*000/,
  );
  assert.match(
    await page.locator("#construction-cost").innerText(),
    /Демонстрационные тарифы/,
  );
  await page.screenshot({
    path: path.join(folder, "construction-cost.png"),
    fullPage: true,
  });
  await page.getByRole("tab", { name: "Обзор", exact: true }).click();
  await page
    .getByRole("heading", { name: "AI-сводка инспекции", exact: true })
    .waitFor();
  const requested = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/objects/${objectId}/analysis`),
  );
  await page
    .getByRole("button", { name: "Обновить AI-сводку", exact: true })
    .click();
  const summaryResponse = await requested;
  assert.equal(summaryResponse.status(), 202);
  const queuedSummary = await summaryResponse.json();
  let summary;
  for (let i = 0; i < 50; i++) {
    summary = await (
      await page.request.get(
        `${base}/api/v1/analyses/${queuedSummary.analysis_id}`,
      )
    ).json();
    if (summary.llm_status !== "pending") break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(summary.llm_status, "unavailable");
  assert.equal(summary.decision_source, "rules_fallback");
  assert.equal(summary.error.code, "MOCK_MODE");
  assert.equal(summary.proposal_only, true);
  await page
    .getByRole("button", { name: "Обновить AI-сводку", exact: true })
    .waitFor();
  assert.deepEqual(forbidden, []);
  assert.deepEqual(failures, []);
  await page.screenshot({
    path: path.join(folder, "connected.png"),
    fullPage: true,
  });
  console.log(
    JSON.stringify({
      passed: true,
      objectId,
      photos: 3,
      targetConfirmed: true,
      readiness: 45,
      material: "brick",
      aiSummary: summary.llm_status,
      constructionCost: {
        full: cost.full_cost_rub,
        completed: cost.completed_cost_rub,
        remaining: cost.remaining_cost_rub,
      },
      unsupportedRequests: forbidden.length,
      errors: failures,
      screenshot: path.join(folder, "connected.png"),
    }),
  );
  const control = await (
    await page.request.post(`${base}/api/v1/objects`, {
      data: { address: "Контрольный объект" },
    })
  ).json();
  const deletedImages = (
    await (
      await page.request.get(
        `${base}/api/v1/flights/${card.last_flight.flight_id}/images`,
      )
    ).json()
  ).map((i) => i.url);
  let deleteRequests = 0;
  page.on("request", (req) => {
    if (req.method() === "DELETE") deleteRequests++;
  });
  const deleteButton = page.getByRole("button", {
    name: "Удалить объект",
    exact: true,
  });
  await deleteButton.click();
  const modal = page.locator("#dialog");
  await modal.getByRole("button", { name: "Отмена", exact: true }).click();
  assert.equal(deleteRequests, 0);
  assert.equal(
    (await page.request.get(`${base}/api/v1/objects/${objectId}`)).status(),
    200,
  );
  await deleteButton.click();
  await page.route(`**/api/v1/objects/${objectId}`, async (route) => {
    if (route.request().method() === "DELETE")
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "OBJECT_BUSY",
            message:
              "Дождитесь завершения обработки фотографий и AI-сводки, затем повторите удаление",
          },
        }),
      });
    else await route.continue();
  });
  await modal
    .getByRole("button", { name: "Удалить объект", exact: true })
    .click();
  await modal.getByRole("alert").waitFor();
  assert.ok(
    (await modal.getByRole("alert").textContent()).includes("Дождитесь"),
  );
  assert.equal(
    (await page.request.get(`${base}/api/v1/objects/${objectId}`)).status(),
    200,
  );
  await page.unroute(`**/api/v1/objects/${objectId}`);
  await modal
    .getByRole("button", { name: "Удалить объект", exact: true })
    .click();
  await page.waitForURL(`${base}/#/objects`);
  await page.getByText("Контрольный объект", { exact: true }).waitFor();
  assert.equal(deleteRequests, 2);
  assert.equal(
    (await page.request.get(`${base}/api/v1/objects/${objectId}`)).status(),
    404,
  );
  assert.equal(
    (await page.request.get(`${base}/api/v1/objects/${control.id}`)).status(),
    200,
  );
  for (const url of deletedImages)
    assert.equal((await page.request.get(base + url)).status(), 404);
  await page.reload();
  await page.getByText("Контрольный объект", { exact: true }).waitFor();
  await page.goto(`${base}/#/objects/${control.id}`);
  await page
    .getByRole("heading", { name: "Контрольный объект", exact: true })
    .waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Удалить объект", exact: true })
    .click();
  await modal
    .getByRole("heading", { name: "Удаление объекта", exact: true })
    .waitFor();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
  );
  await page.screenshot({
    path: path.join(folder, "delete-mobile.png"),
    animations: "disabled",
  });
  await modal.getByRole("button", { name: "Отмена", exact: true }).click();
  const demoContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: "ru-RU",
  });
  await demoContext.addInitScript(() =>
    localStorage.setItem(
      "sibcode.front.settings",
      JSON.stringify({
        mode: "demo",
        user: "test",
        display_name: "Проверяющий",
      }),
    ),
  );
  const demoPage = await demoContext.newPage();
  demoPage.on("pageerror", (e) => failures.push(e.message));
  await demoPage.goto(`${base}/#/objects/obj_demo_1`);
  await demoPage
    .getByRole("button", { name: "Удалить объект", exact: true })
    .click();
  await demoPage
    .locator("#dialog")
    .getByRole("button", { name: "Удалить объект", exact: true })
    .click();
  await demoPage.waitForURL(`${base}/#/objects`);
  await demoPage.reload();
  await demoPage
    .getByRole("heading", { name: "Объекты", exact: true })
    .waitFor();
  const missing = await demoPage.evaluate(async () => {
    const api = await import("/js/api.js");
    try {
      await api.request("/objects/obj_demo_1");
      return false;
    } catch (e) {
      return e.code === "NOT_FOUND";
    }
  });
  assert.equal(missing, true);
  assert.deepEqual(failures, []);
  console.log(
    JSON.stringify({
      deletionBrowser: true,
      cancelPreservesObject: true,
      busyShowsError: true,
      deletionPersists: true,
      otherObjectPreserved: true,
      photosUnavailable: true,
      mobileNoOverflow: true,
      demoDeletionPersists: true,
      errors: failures,
    }),
  );
  await demoContext.close();
} catch (error) {
  console.error(logs.join("").slice(-7000));
  throw error;
} finally {
  if (browser) await browser.close();
  await Promise.all(
    children.map(
      (child) =>
        new Promise((resolve) => {
          if (child.exitCode !== null) return resolve();
          child.once("exit", resolve);
          if (process.platform === "win32") {
            try {
              execFileSync(
                "taskkill.exe",
                ["/PID", String(child.pid), "/T", "/F"],
                { windowsHide: true, stdio: "ignore" },
              );
            } catch {
              child.kill();
            }
          } else child.kill();
        }),
    ),
  );
}
