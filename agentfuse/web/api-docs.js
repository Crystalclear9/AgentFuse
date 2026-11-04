"use strict";
(async () => {
  const root = document.getElementById("api-documentation");
  try {
    const response = await fetch("/api/openapi.json");
    if (!response.ok) throw new Error(`接口定义读取失败：${response.status}`);
    const schema = await response.json();
    root.textContent = "";
    for (const [path, methods] of Object.entries(schema.paths)) {
      for (const [method, operation] of Object.entries(methods)) {
        if (!["get", "post", "put", "patch", "delete", "options", "head"].includes(method)) continue;
        const section = document.createElement("section");
        const title = document.createElement("h2");
        title.textContent = `${method.toUpperCase()} ${path}`;
        section.appendChild(title);
        const description = document.createElement("p");
        description.textContent = operation.description || operation.summary || "";
        section.appendChild(description);
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "参数、请求体和响应定义";
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(operation, null, 2);
        details.appendChild(summary);
        details.appendChild(pre);
        section.appendChild(details);
        root.appendChild(section);
      }
    }
  } catch (error) { root.textContent = String(error.message || error); }
})();
