/**
 * useAtlasDaemon
 * --------------
 * Core WebSocket connection to the Atlas VPS Brain.
 *
 * Key fixes over previous version:
 *  1. Connection guard checks CONNECTING state too — prevents 4 simultaneous connections.
 *  2. Generation counter — each connect() call gets a unique ID. The onclose handler
 *     only acts if its generation matches the current one, eliminating the
 *     intentionalClose ref race condition that caused clean-disconnect loops.
 *  3. Belt-and-suspenders client ping every 25s keeps NAT middleboxes warm.
 *
 * Protocol:
 *   RECEIVE: { type: "ping" }              → reply { type: "pong" }
 *   RECEIVE: { task_id, tool, kwargs }     → execute tool, reply { task_id, status, result }
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { TOOL_REGISTRY } from '../tools';

export type DaemonStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

interface AtlasTask {
  task_id: string;
  tool: string;
  kwargs: Record<string, unknown>;
}

interface UseDaemonOptions {
  vpsUrl: string;
  workerToken: string;
  workerName?: string;
}

const CLIENT_PING_INTERVAL_MS = 25_000;

export function useAtlasDaemon({
  vpsUrl,
  workerToken,
  workerName = 'duke-android',
}: UseDaemonOptions) {
  const ws = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<DaemonStatus>('disconnected');
  const [lastTool, setLastTool] = useState<string | null>(null);

  // ── Generation counter ───────────────────────────────────────────────────────
  // Every new connect() call increments this. Stale onclose handlers compare
  // their captured generation against the current value — if they don't match,
  // they belong to a superseded connection and do nothing.
  const generation = useRef(0);

  const reconnectDelay = useRef(3000);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearPingTimer = useCallback(() => {
    if (pingTimer.current) { clearInterval(pingTimer.current); pingTimer.current = null; }
  }, []);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimer.current) { clearTimeout(reconnectTimer.current); reconnectTimer.current = null; }
  }, []);

  // ── Tool execution ───────────────────────────────────────────────────────────
  const executeTask = useCallback(async (task: AtlasTask) => {
    const { task_id, tool, kwargs } = task;
    setLastTool(tool);
    console.log(`[Atlas] Executing tool: ${tool}`, kwargs);

    const handler = TOOL_REGISTRY[tool];
    if (!handler) {
      ws.current?.send(JSON.stringify({ task_id, status: 'error', result: `Unknown tool: ${tool}` }));
      return;
    }
    try {
      const result = await handler(kwargs);
      ws.current?.send(JSON.stringify({ task_id, status: 'success', result }));
      console.log(`[Atlas] Tool ${tool} completed.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      ws.current?.send(JSON.stringify({ task_id, status: 'error', result: `Tool error: ${message}` }));
      console.error(`[Atlas] Tool ${tool} failed:`, message);
    }
  }, []);

  // ── Connection ───────────────────────────────────────────────────────────────
  const connect = useCallback(() => {
    // Guard: block if already OPEN or CONNECTING (readyState 0 or 1)
    const state = ws.current?.readyState;
    if (state === WebSocket.OPEN || state === WebSocket.CONNECTING) {
      console.log('[Atlas] Connection already active — skipping duplicate connect.');
      return;
    }

    // Claim this generation so stale handlers know they're obsolete
    generation.current += 1;
    const myGeneration = generation.current;

    const uri = `${vpsUrl}/ws?token=${workerToken}`;
    console.log(`[Atlas] Connecting (gen ${myGeneration}) to ${uri}`);
    setStatus('connecting');

    const socket = new WebSocket(uri);
    ws.current = socket;

    socket.onopen = () => {
      if (generation.current !== myGeneration) { socket.close(); return; }
      console.log(`[Atlas] Connected (gen ${myGeneration}).`);

      socket.send(JSON.stringify({ type: 'identity', worker_type: 'mobile', name: workerName }));
      setStatus('connected');
      reconnectDelay.current = 3000;

      clearPingTimer();
      pingTimer.current = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ping' }));
          console.debug('[Atlas] Sent keepalive ping.');
        }
      }, CLIENT_PING_INTERVAL_MS);
    };

    socket.onmessage = (event) => {
      if (generation.current !== myGeneration) return;
      try {
        const data = JSON.parse(event.data as string);
        if (data.type === 'ping') {
          socket.send(JSON.stringify({ type: 'pong' }));
          console.debug('[Atlas] ping → pong');
          return;
        }
        if (data.task_id && data.tool) {
          executeTask(data as AtlasTask);
        }
      } catch (e) {
        console.warn('[Atlas] Failed to parse message:', e);
      }
    };

    socket.onerror = () => {
      if (generation.current !== myGeneration) return;
      console.error('[Atlas] WebSocket error.');
      setStatus('error');
    };

    socket.onclose = (event) => {
      clearPingTimer();

      // If this isn't the current generation, a newer connection has taken over — do nothing.
      if (generation.current !== myGeneration) {
        console.debug(`[Atlas] Stale onclose (gen ${myGeneration}) — ignored.`);
        return;
      }

      setStatus('disconnected');
      console.log(`[Atlas] Connection closed — code: ${event.code} reason: ${event.reason || 'none'}`);

      // code 1000 = normal closure initiated by us (intentional)
      // code 1001 = server going away (ping timeout)
      // Anything else = unexpected — reconnect
      const shouldReconnect = event.code !== 1000;
      if (shouldReconnect) {
        console.log(`[Atlas] Reconnecting in ${reconnectDelay.current / 1000}s…`);
        clearReconnectTimer();
        reconnectTimer.current = setTimeout(() => {
          reconnectDelay.current = Math.min(reconnectDelay.current * 2, 60_000);
          connect();
        }, reconnectDelay.current);
      }
    };
  }, [vpsUrl, workerToken, workerName, executeTask, clearPingTimer, clearReconnectTimer]);

  // ── Manual disconnect (code 1000 = intentional, no reconnect) ────────────────
  const disconnect = useCallback(() => {
    generation.current += 1; // Invalidate any pending onclose handlers
    clearPingTimer();
    clearReconnectTimer();
    ws.current?.close(1000, 'User disconnected');
    setStatus('disconnected');
  }, [clearPingTimer, clearReconnectTimer]);

  // ── Lifecycle ────────────────────────────────────────────────────────────────
  useEffect(() => {
    connect();
    return () => {
      // Increment generation so onclose from this connection does not reconnect
      generation.current += 1;
      clearPingTimer();
      clearReconnectTimer();
      ws.current?.close(1000, 'Component unmounted');
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Empty deps — connect once on mount, clean up on unmount. Period.

  return { status, lastTool, connect, disconnect };
}
