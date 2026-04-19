/**
 * Atlas WhatsApp Service — Layer 1 Stub
 * ----------------------------------------
 * Baileys Node.js sidecar for WhatsApp integration.
 * Full implementation (QR auth, send/receive, smart inbox) comes in Layer 6.
 * This stub exposes a minimal HTTP server so docker-compose Layer 1 starts cleanly.
 */

const express = require("express");

const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

// Liveness probe
app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "atlas-whatsapp", layer: 1 });
});

app.listen(PORT, () => {
  console.log(`[atlas.whatsapp] Layer 1 stub running on port ${PORT}`);
  console.log("[atlas.whatsapp] Baileys integration will be activated in Layer 6.");
});
