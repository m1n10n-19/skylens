// Streaming client for POST /analyze/stream (server-sent events over fetch).
(function (SL) {

  // Slightly above the backend's worst case
  // (Overpass budget 100 s + DeepSeek, geocoding, imagery).
  SL.TIMEOUT_MS = 150000;

  function apiError(status, body) {
    const d = body && body.detail;
    if (d && typeof d === "object" && !Array.isArray(d)) {
      return {stage: d.stage || "unknown", text: d.error || d.message || "", status};
    }
    return {stage: "unknown", text: typeof d === "string" ? d : `Request failed (${status}).`, status};
  }

  // Parse one SSE block ("event: x\ndata: {...}") into [event, data].
  function parseBlock(block) {
    let event = "message";
    const data = [];
    block.split("\n").forEach(line => {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    });
    return data.length ? [event, JSON.parse(data.join("\n"))] : null;
  }

  // Resolves with the final /analyze response; rejects with
  // {stage, text}. onEvent(event, data) receives progress events.
  SL.analyzeStream = async (query, radiusKm, onEvent) => {

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SL.TIMEOUT_MS);
    const fail = e => ({stage: e && e.name === "AbortError" ? "timeout" : "network", text: ""});

    let response;
    try {
      response = await fetch(SL.API + "/analyze/stream", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({query, radius_km: radiusKm || null}),
        signal: controller.signal,
      });
    } catch (e) {
      clearTimeout(timer);
      throw fail(e);
    }

    if (!response.ok) {
      clearTimeout(timer);
      throw apiError(response.status, await response.json().catch(() => null));
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result = null;
    let error = null;

    try {
      for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true}).replace(/\r\n/g, "\n");
        let cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const parsed = parseBlock(buffer.slice(0, cut));
          buffer = buffer.slice(cut + 2);
          if (!parsed) continue;
          const [event, data] = parsed;
          if (event === "result") result = data;
          else if (event === "error") error = data;
          else onEvent(event, data);
        }
      }
    } catch (e) {
      throw fail(e);
    } finally {
      clearTimeout(timer);
    }

    if (error) throw apiError(error.status_code, error);
    if (!result) throw {stage: "network", text: "The connection closed before the analysis finished."};
    return result;
  };

})(window.SL);
