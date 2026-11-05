import test from "node:test";
import assert from "node:assert/strict";
import { objectDeletionDialog } from "../js/object-deletion.js";

const values = new Map();
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
  removeItem: (key) => values.delete(key),
};
const { mockRequest, resetDemo } = await import("../js/mock.js");
const esc = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );

test("confirmation names the object, escapes its data and provides separate cancel and delete buttons", () => {
  const html = objectDeletionDialog(
    { id: 'obj_" onmouseover="evil', address: "<img src=x onerror=evil>" },
    esc,
  );
  assert.ok(html.includes("&lt;img"));
  assert.ok(!html.includes("<img"));
  assert.ok(
    html.includes('type="button"') &&
      html.includes('data-action="close-dialog"'),
  );
  assert.ok(html.includes('type="submit" class="btn danger"'));
  assert.ok(html.includes("фотографии") && html.includes("AI-сводки"));
});

test("demo deletion persists and removes only the requested object and its flights", async () => {
  resetDemo();
  const before = await mockRequest("/objects?page_size=100");
  const obj = await mockRequest("/objects/obj_demo_1");
  assert.deepEqual(
    await mockRequest("/objects/obj_demo_1", { method: "DELETE" }),
    { id: obj.id, deleted: true },
  );
  assert.equal(
    (await mockRequest("/objects?page_size=100")).total,
    before.total - 1,
  );
  await assert.rejects(() => mockRequest("/objects/obj_demo_1"), {
    code: "NOT_FOUND",
  });
  assert.ok(await mockRequest("/objects/obj_demo_2"));
  const reloaded = await import("../js/mock.js?deletion-reload");
  await assert.rejects(() => reloaded.mockRequest("/objects/obj_demo_1"), {
    code: "NOT_FOUND",
  });
  for (const flight of obj.flights)
    await assert.rejects(() => mockRequest(`/flights/${flight.flight_id}`), {
      code: "NOT_FOUND",
    });
});
