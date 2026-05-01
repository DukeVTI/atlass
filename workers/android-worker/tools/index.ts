/**
 * tools/index.ts — Atlas Mobile Tool Registry
 * --------------------------------------------
 * Maps tool names (sent by the VPS Brain) to local handler functions.
 * Add new tools here — the daemon picks them up automatically.
 */

import { speak, stopSpeaking } from './tts';
import { getLocation } from './location';
import { readSMS, sendSMS } from './sms';
import { pushNotification, readNotifications } from './notifications';
import { getDeviceStats, readContacts } from './device';

type ToolHandler = (args: Record<string, unknown>) => Promise<unknown>;

export const TOOL_REGISTRY: Record<string, ToolHandler> = {
  // ── Audio / Voice ────────────────────────────────
  speak,
  stop_speaking: stopSpeaking,

  // ── Location ─────────────────────────────────────
  get_location: getLocation,

  // ── SMS ──────────────────────────────────────────
  read_sms: readSMS,
  send_sms: sendSMS,

  // ── Notifications ────────────────────────────────
  push_notification: pushNotification,
  read_notifications: readNotifications,

  // ── Device ───────────────────────────────────────
  get_device_stats: getDeviceStats,

  // ── Contacts ─────────────────────────────────────
  read_contacts: readContacts,
};

export type ToolName = keyof typeof TOOL_REGISTRY;
