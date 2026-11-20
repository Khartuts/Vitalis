// Mock OpenAI Responses API (SSE) used only by tests. Round 1 requests a tool; round 2 answers citing the tool's evidence.
import http from "node:http";

export function startMockOpenAI() {
  const requests = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      if (req.method === "GET" && req.url === "/v1/models") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ data: [{ id: "gpt-test-analyst" }, { id: "dall-e-3" }] }));
        return;
      }
      if (req.method !== "POST" || req.url !== "/v1/responses") { res.writeHead(404).end(); return; }
      const json = JSON.parse(body);
      requests.push({ headers: req.headers, body: json });
      res.writeHead(200, { "content-type": "text/event-stream" });
      const send = (o) => res.write(`event: ${o.type}\ndata: ${JSON.stringify(o)}\n\n`);
      const output = json.input.find((i) => i.type === "function_call_output");
      if (!output) {
        const endD = new Date(Date.now() - 86400000);
        const startD = new Date(endD.getTime() - 29 * 86400000);
        const args = JSON.stringify({ metric: "rhr", start: startD.toISOString().slice(0, 10), end: endD.toISOString().slice(0, 10), resolution: "daily" });
        send({ type: "response.created", response: { id: "resp_1" } });
        send({ type: "response.output_item.added", output_index: 0, item: { type: "function_call", id: "fc_1", call_id: "call_1", name: "get_metric_history", arguments: "" } });
        send({ type: "response.function_call_arguments.delta", item_id: "fc_1", output_index: 0, delta: args });
        send({ type: "response.output_item.done", output_index: 0, item: { type: "function_call", id: "fc_1", call_id: "call_1", name: "get_metric_history", arguments: args } });
        send({ type: "response.completed", response: { id: "resp_1", status: "completed" } });
        res.end();
        return;
      }
      const result = JSON.parse(output.output);
      const ev = result.evidence?.[0]?.id ?? "missing";
      const s = result.summary ?? {};
      const text = `### Answer\nYour resting heart rate averaged ${s.mean?.toFixed?.(1) ?? "n/a"} bpm over ${s.n ?? 0} nights [[ev:${ev}]].\n`
        + `### Uncertainty & limitations\n- ${s.missing_days ?? 0} days had no data.\n- Wrist sensors are estimates.`;
      send({ type: "response.created", response: { id: "resp_2" } });
      for (const chunk of text.match(/.{1,40}/gs)) send({ type: "response.output_text.delta", delta: chunk });
      send({ type: "response.completed", response: { id: "resp_2", status: "completed" } });
      res.end();
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, requests, port: server.address().port })));
}
