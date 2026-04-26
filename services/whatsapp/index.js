/**
 * Atlas WhatsApp Service
 * ----------------------------------------
 * Baileys Node.js sidecar for WhatsApp integration.
 * - Handles authentication (QR code on first run, saves session)
 * - Exposes POST /send for outbound messages
 * - Listens to incoming messages and forwards to Python API via webhook
 */

const express = require("express");
const axios = require("axios");
const pino = require("pino");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  Browsers,
} = require("@whiskeysockets/baileys");
const qrcode = require("qrcode-terminal");

const app = express();
const PORT = process.env.PORT || 3000;
const API_WEBHOOK_URL = process.env.API_URL 
  ? `${process.env.API_URL}/webhooks/whatsapp` 
  : "http://api:8000/webhooks/whatsapp";

app.use(express.json());

// Global socket instance
let sock = null;

async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState("baileys_auth_info");
  const { version, isLatest } = await fetchLatestBaileysVersion();
  console.log(`[atlas.whatsapp] Using WA v${version.join(".")}, isLatest: ${isLatest}`);

  sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger: pino({ level: "silent" }),
    browser: Browsers.macOS('Desktop'),
    syncFullHistory: false,
  });

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;
    
    if (qr) {
      console.log("[atlas.whatsapp] Scan this QR code in WhatsApp Linked Devices:");
      qrcode.generate(qr, { small: true });
    }
    
    if (connection === "close") {
      const shouldReconnect =
        lastDisconnect.error?.output?.statusCode !== DisconnectReason.loggedOut;
      console.log(
        "[atlas.whatsapp] Connection closed due to",
        lastDisconnect.error,
        ", reconnecting:",
        shouldReconnect
      );
      if (shouldReconnect) {
        connectToWhatsApp();
      }
    } else if (connection === "open") {
      console.log("[atlas.whatsapp] Connected successfully to WhatsApp!");
    }
  });

  sock.ev.on("creds.update", saveCreds);

  // Inbound message handling
  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const msg of messages) {
      // Ignore messages sent by us
      if (msg.key.fromMe) continue;
      
      const remoteJid = msg.key.remoteJid;
      
      // Ignore group chats and status updates for now
      if (remoteJid.endsWith("@g.us") || remoteJid === "status@broadcast") continue;
      
      const senderName = msg.pushName || "Unknown";
      
      // Extract text from standard message or extended text message
      const messageText = 
        msg.message?.conversation || 
        msg.message?.extendedTextMessage?.text || 
        "";
        
      if (!messageText) continue;

      console.log(`[atlas.whatsapp] Received message from ${senderName} (${remoteJid})`);
      
      // Forward to Python API
      try {
        await axios.post(API_WEBHOOK_URL, {
          remote_jid: remoteJid,
          sender_name: senderName,
          message_text: messageText,
        });
      } catch (err) {
        console.error(`[atlas.whatsapp] Failed to forward message to API: ${err.message}`);
      }
    }
  });
}

// ─── API Routes ────────────────────────────────────────────────────────────

// Liveness probe
app.get("/health", (_req, res) => {
  res.json({ 
    status: "ok", 
    service: "atlas-whatsapp", 
    connected: !!sock && !!sock.user 
  });
});

// Outbound send endpoint
app.post("/send", async (req, res) => {
  const { remote_jid, text } = req.body;
  
  if (!remote_jid || !text) {
    return res.status(400).json({ error: "Missing remote_jid or text" });
  }
  
  if (!sock) {
    return res.status(503).json({ error: "WhatsApp not connected yet" });
  }

  try {
    // Basic number formatting - append @s.whatsapp.net if just a number
    let clean_jid = remote_jid.replace(/[^0-9@.s_A-Za-z-]/g, ""); // Strip +, spaces, etc.
    const jid = clean_jid.includes("@") ? clean_jid : `${clean_jid}@s.whatsapp.net`;
    
    // Check if the number actually exists on WhatsApp
    const [result] = await sock.onWhatsApp(jid);
    if (!result || !result.exists) {
      return res.status(404).json({ error: "Number is not registered on WhatsApp" });
    }
    
    await sock.sendMessage(jid, { text });
    console.log(`[atlas.whatsapp] Sent message to ${jid}`);
    res.json({ status: "sent", jid });
  } catch (err) {
    console.error(`[atlas.whatsapp] Error sending message: ${err.message}`);
    res.status(500).json({ error: err.message });
  }
});

// ─── Startup ────────────────────────────────────────────────────────────────

app.listen(PORT, () => {
  console.log(`[atlas.whatsapp] Service listening on port ${PORT}`);
  connectToWhatsApp();
});
