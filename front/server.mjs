import http from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL(".", import.meta.url));
const mime = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};
const port = Number(process.env.PORT || 4173);
http
  .createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname.startsWith("/api/v1/")) {
      if (!process.env.API_TARGET) {
        res.writeHead(503, {
          "Content-Type": "application/json; charset=utf-8",
        });
        res.end(
          JSON.stringify({
            error: {
              code: "API_NOT_CONFIGURED",
              message:
                "API не подключён. Укажите API_TARGET при запуске сервера или выберите демо-режим.",
            },
          }),
        );
        return;
      }
      try {
        const target = new URL(
          url.pathname + url.search,
          process.env.API_TARGET,
        );
        const chunks = [];
        for await (const c of req) chunks.push(c);
        const headers = {};
        for (const key of ["content-type", "x-user", "accept"])
          if (req.headers[key]) headers[key] = req.headers[key];
        const result = await fetch(target, {
          method: req.method,
          headers,
          body: ["GET", "HEAD"].includes(req.method)
            ? undefined
            : Buffer.concat(chunks),
          signal: AbortSignal.timeout(30000),
        });
        res.writeHead(result.status, {
          "Content-Type":
            result.headers.get("content-type") || "application/json",
          "Cache-Control": "no-store",
        });
        res.end(Buffer.from(await result.arrayBuffer()));
      } catch {
        res.writeHead(502, {
          "Content-Type": "application/json; charset=utf-8",
        });
        res.end(
          JSON.stringify({
            error: {
              code: "API_UNAVAILABLE",
              message:
                "Бэкенд недоступен. Проверьте API_TARGET и повторите запрос.",
            },
          }),
        );
      }
      return;
    }
    let pathname;
    try {
      pathname = decodeURIComponent(url.pathname);
    } catch {
      res.writeHead(400);
      res.end("Bad request");
      return;
    }
    if (
      !["GET", "HEAD"].includes(req.method) ||
      !(
        pathname === "/" ||
        pathname === "/index.html" ||
        /^\/(css|js|assets)\/[\w/.-]+$/.test(pathname)
      ) ||
      pathname.includes("..")
    ) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    try {
      const file = path.join(root, pathname === "/" ? "index.html" : pathname);
      const data = await readFile(file);
      res.writeHead(200, {
        "Content-Type": mime[path.extname(file)] || "application/octet-stream",
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
      });
      res.end(req.method === "HEAD" ? undefined : data);
    } catch {
      res.writeHead(404);
      res.end("Not found");
    }
  })
  .listen(port, "127.0.0.1", () =>
    console.log(`Дрон-мониторинг: http://127.0.0.1:${port}`),
  );
