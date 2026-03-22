/**
 * puter_server.js
 * Local Express microservice that bridges Python (ai_engine.py) to Puter.js AI.
 * Runs on http://localhost:3000 and exposes a single POST /chat endpoint.
 *
 * Puter.js gives keyless, unlimited access to llama-3.3-70b — no API keys needed.
 */

const express = require("express");

// Puter.js is a browser-first SDK; we load it in Node.js via a lightweight shim.
// The package ships a node-compatible entry that sets up a global `puter` object.
const puter = require("@puter/puter.js");

const app = express();
app.use(express.json());

const PORT = process.env.PUTER_PORT || 3000;
const MODEL = "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"; // Puter alias for Llama-3.3-70b

/**
 * POST /chat
 * Body: { system_prompt: string, user_message: string, max_tokens?: number }
 * Returns: { reply: string }
 */
app.post("/chat", async (req, res) => {
  const { system_prompt, user_message, max_tokens = 300 } = req.body;

  if (!system_prompt || !user_message) {
    return res.status(400).json({ error: "system_prompt and user_message are required." });
  }

  try {
    // puter.ai.chat() accepts an array of messages (OpenAI-compatible format).
    const response = await puter.ai.chat(
      [
        { role: "system", content: system_prompt },
        { role: "user",   content: user_message  },
      ],
      {
        model:      MODEL,
        max_tokens: max_tokens,
        temperature: 0.85,
        top_p:       0.9,
      }
    );

    // Puter returns { message: { content: "..." } } or a raw string depending on version.
    const reply =
      typeof response === "string"
        ? response
        : response?.message?.content ?? response?.content ?? "NO_REPLY";

    return res.json({ reply: reply.trim() });
  } catch (err) {
    console.error("[puter_server] AI call failed:", err?.message || err);
    // Return NO_REPLY so Python side handles it gracefully (same as Groq error path).
    return res.status(500).json({ reply: "NO_REPLY", error: String(err?.message || err) });
  }
});

// Health probe used by render.yaml
app.get("/health", (_req, res) => res.json({ status: "ok", service: "puter_server" }));

app.listen(PORT, "127.0.0.1", () => {
  console.log(`[puter_server] Listening on http://127.0.0.1:${PORT}`);
  console.log(`[puter_server] Model: ${MODEL} (keyless via Puter.js)`);
});
